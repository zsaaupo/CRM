"""OPS-01..07 services: settings audit, notifications, outbox delivery, custom fields, payment gateways, backups."""

from datetime import timedelta
from decimal import Decimal
import hashlib
import hmac
import os
from pathlib import Path
import shutil
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from common.audit import log_audit_event
from common.exceptions import StateConflict
from common.models import (
    AuditLog, BackupSetting, CustomField, CustomFieldValue, GeneralSetting,
    HRMSetting, MailSetting, Notification, NotificationRecipient, OutboxMessage,
    POSSetting, PaymentAttempt, PaymentGatewayConfig, RewardSetting, SMSSetting,
    TaskSetting,
)
from common.money import q_money
from common.settings_store import invalidate_general_cache
from warehouse.scope import check_warehouse, warehouse_scope


User = get_user_model()
REDACTED_FIELDS = {'password_encrypted', 'api_key_encrypted', 'credentials_encrypted'}


def update_singleton_setting(actor, model_cls, updates: dict, *, request=None):
    with transaction.atomic():
        obj, _ = model_cls.objects.get_or_create(pk=1)
        if model_cls is POSSetting and updates.get('default_warehouse'):
            wh_id = updates['default_warehouse'].pk if hasattr(updates['default_warehouse'], 'pk') else int(updates['default_warehouse'])
            check_warehouse(warehouse_scope(actor), wh_id)

        changes = {}
        username = actor.get_username()
        for field, val in updates.items():
            if not hasattr(obj, field):
                continue
            old = getattr(obj, field)
            if str(old) != str(val):
                setattr(obj, field, val)
                if field in REDACTED_FIELDS:
                    changes[field] = {'old': '***', 'new': '***'}
                else:
                    changes[field] = {'old': str(old), 'new': str(val)}
        obj.modified_by = username
        if not obj.create_by:
            obj.create_by = username
        obj.save()
        if model_cls is GeneralSetting:
            invalidate_general_cache()
        if changes:
            log_audit_event(actor, AuditLog.Action.SETTINGS_CHANGE, obj, changes=changes, request=request, module='settings')
        return obj


def publish_notification(actor, *, title: str, body: str, audience: str = 'all', user_ids: list[int] | None = None) -> Notification:
    with transaction.atomic():
        username = actor.get_username() if actor else 'system'
        notif = Notification.objects.create(
            title=title, body=body, audience=audience, created_by=actor,
            create_by=username, modified_by=username,
        )
        qs = User.objects.filter(pk__in=user_ids, is_active=True) if user_ids else User.objects.filter(is_active=True)
        NotificationRecipient.objects.bulk_create([
            NotificationRecipient(notification=notif, user=u) for u in qs
        ], ignore_conflicts=True)
        return notif


def enqueue_outbox_message(*, channel: str, recipient: str, source_event_key: str,
                           payload: dict | None = None, template: str = '') -> tuple[OutboxMessage, bool]:
    try:
        with transaction.atomic():
            msg, created = OutboxMessage.objects.get_or_create(
                source_event_key=source_event_key,
                defaults={
                    'channel': channel,
                    'template': template or '',
                    'recipient': recipient,
                    'payload': payload or {},
                    'status': OutboxMessage.Status.PENDING,
                    'create_by': 'system',
                    'modified_by': 'system',
                },
            )
            return msg, created
    except IntegrityError:
        return OutboxMessage.objects.get(source_event_key=source_event_key), False


def process_outbox_messages(*, max_attempts: int = 5, transport=None) -> dict:
    now = timezone.now()
    due = list(
        OutboxMessage.objects.filter(status=OutboxMessage.Status.PENDING)
        .filter(Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now))
        .order_by('created_at', 'pk')[:100]
    )
    sent = 0
    failed = 0
    for msg in due:
        with transaction.atomic():
            row = OutboxMessage.objects.select_for_update().get(pk=msg.pk)
            if row.status != OutboxMessage.Status.PENDING:
                continue
            row.attempts += 1
            try:
                if transport is not None:
                    transport(row)
                row.status = OutboxMessage.Status.SENT
                row.last_error = ''
                row.next_retry_at = None
                sent += 1
            except Exception as exc:
                row.last_error = str(exc)[:500]
                if row.attempts >= max_attempts:
                    row.status = OutboxMessage.Status.FAILED
                    row.next_retry_at = None
                    failed += 1
                else:
                    row.next_retry_at = now + timedelta(minutes=2 ** row.attempts)
            row.save(update_fields=['attempts', 'status', 'last_error', 'next_retry_at', 'updated_at'])
    return {'processed': len(due), 'sent': sent, 'failed': failed}


def validate_and_save_custom_fields(entity: str, object_id: int, values: dict, *, is_create: bool = False) -> dict:
    fields = {f.key: f for f in CustomField.objects.filter(entity=entity, is_active=True)}
    saved = {}
    if is_create:
        for key, cf in fields.items():
            if cf.required and key not in values and cf.default is None:
                raise ValidationError({'custom_fields': f'Custom field "{cf.label}" is required.'})
    for key, val in (values or {}).items():
        cf = fields.get(key)
        if not cf:
            continue
        CustomFieldValue.objects.update_or_create(
            field=cf, object_id=object_id, defaults={'value': val},
        )
        saved[key] = val
    return saved


def initiate_gateway_payment(actor, *, provider: str, order_id: int, amount, account_id: int | None = None,
                             request_key=None) -> dict:
    cfg = PaymentGatewayConfig.objects.filter(provider=provider, enabled=True).first()
    if cfg is None:
        raise ValidationError({'provider': f'Payment gateway "{provider}" is not enabled.'})
    from sale.models import SalesOrder
    order = SalesOrder.objects.filter(pk=order_id).first()
    if order is None:
        raise NotFound('Sales order not found.')
    amt = q_money(amount, digits=4)
    if amt <= Decimal('0'):
        raise ValidationError({'amount': 'Payment amount must be positive.'})
    rk = request_key or uuid4()
    attempt, _ = PaymentAttempt.objects.get_or_create(
        request_key=rk,
        defaults={
            'provider': provider,
            'order': order,
            'account_id': account_id,
            'amount': amt,
            'currency': order.currency_id or settings.INVENTORY_BASE_CURRENCY,
            'provider_ref': f'{provider.upper()}-{rk.hex[:12].upper()}',
            'create_by': actor.get_username() if actor else 'system',
            'modified_by': actor.get_username() if actor else 'system',
        },
    )
    redirect_url = f'/sandbox/{provider}/checkout/?ref={attempt.provider_ref}&amount={attempt.amount}'
    return {
        'attempt_id': attempt.pk,
        'provider': attempt.provider,
        'provider_ref': attempt.provider_ref,
        'status': attempt.status,
        'redirect_url': redirect_url,
        'request_key': str(attempt.request_key),
    }


def verify_and_settle_gateway_callback(actor, *, provider_ref: str, signature: str, payload: dict) -> dict:
    with transaction.atomic():
        attempt = PaymentAttempt.objects.select_for_update().filter(provider_ref=provider_ref).first()
        if attempt is None:
            raise NotFound('Payment attempt not found.')
        cfg = PaymentGatewayConfig.objects.filter(provider=attempt.provider, enabled=True).first()
        if cfg is None:
            raise ValidationError({'provider': 'Gateway is disabled.'})

        secret = (cfg.credentials_encrypted or 'sandbox-secret').encode()
        expected = hmac.new(secret, f'{attempt.provider_ref}:{attempt.amount}'.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(str(signature or ''), expected):
            attempt.status = PaymentAttempt.Status.FAILED
            attempt.save(update_fields=['status', 'updated_at'])
            raise ValidationError({'signature': 'Invalid gateway callback signature.'})

        if attempt.status == PaymentAttempt.Status.SUCCEEDED and attempt.callback_verified:
            return {'attempt_id': attempt.pk, 'status': attempt.status, 'idempotent_replay': True}

        from accounting.models import FinancialAccount
        from accounting.services import post_sales_payment

        acct_id = attempt.account_id
        if not acct_id:
            default_acc = FinancialAccount.objects.filter(is_active=True, currency=attempt.order.currency_id).order_by('-is_default', 'pk').first()
            if default_acc is None:
                raise ValidationError({'account': 'No active financial account for gateway settlement.'})
            acct_id = default_acc.pk

        res = post_sales_payment(
            actor,
            attempt.order_id,
            [{'method': 'bank_transfer', 'account_id': acct_id, 'amount': attempt.amount, 'note': f'Gateway {attempt.provider_ref}'}],
            timezone.localdate(),
            attempt.request_key,
        )
        attempt.status = PaymentAttempt.Status.SUCCEEDED
        attempt.callback_verified = True
        attempt.save(update_fields=['status', 'callback_verified', 'updated_at'])
        return {
            'attempt_id': attempt.pk,
            'status': attempt.status,
            'payment_ids': res.payment_ids,
            'idempotent_replay': False,
        }


def run_database_backup(actor) -> dict:
    if not actor or not actor.is_superuser:
        raise PermissionDenied('Only superusers may trigger database backups.')
    cfg, _ = BackupSetting.objects.get_or_create(pk=1)
    flag_enabled = bool(getattr(settings, 'ENABLE_DB_BACKUP', False) or cfg.enabled)
    if not flag_enabled:
        raise StateConflict('Database backup is disabled by policy (enable BackupSetting or ENABLE_DB_BACKUP).')

    db_conf = settings.DATABASES['default']
    engine = db_conf.get('ENGINE', '')
    target_dir = Path(cfg.backup_dir or os.path.join(settings.BASE_DIR, 'backups'))
    target_dir.mkdir(parents=True, exist_ok=True)
    ts = timezone.now().strftime('%Y%m%d_%H%M%S')

    if 'sqlite' in engine:
        src = Path(str(db_conf['NAME']))
        dest = target_dir / f'emanager_backup_{ts}.sqlite3'
        if src.exists():
            shutil.copy2(src, dest)
        else:
            dest.write_bytes(b'')
    else:
        raise ValidationError({'engine': 'Automated backup helper currently supports SQLite directly; use pg_dump in ops.'})

    log_audit_event(actor, AuditLog.Action.EXPORT, cfg, changes={'backup_file': dest.name}, module='settings')
    return {'file': str(dest), 'created_at': timezone.now().isoformat()}
