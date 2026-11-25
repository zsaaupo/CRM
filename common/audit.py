from decimal import Decimal
from uuid import UUID

from .models import AuditLog


REDACT_TOKENS = ('password', 'secret', 'token', 'api_key', 'private_key', 'card_number', 'cvv', 'cvc', 'pin')

AUDIT_FIELDS = {
    'common.GeneralSetting': {
        'system_title', 'rtl_layout', 'company_name', 'vat_registration_number', 'timezone',
        'allow_sale_without_stock', 'enable_packing_slip', 'enable_in_house_production',
        'staff_access', 'costing_method', 'restore_coupon_on_full_return', 'block_expired_batch_sale',
        'default_currency_id', 'currency_position', 'decimal_digits', 'invoice_format',
        'date_format', 'expiry_duration_type', 'expiry_value',
    },
    'accounting.SalesPayment': {'sales_order_id', 'sales_return_id', 'account_id', 'amount', 'method', 'date', 'origin'},
    'accounting.PurchasePayment': {'purchase_order_id', 'purchase_return_id', 'account_id', 'amount', 'method', 'date', 'origin'},
    'accounting.FinancialAccount': {'name', 'type', 'warehouse_id', 'is_active', 'is_default', 'account_number'},
    'sale.SalesOrder': {'customer_id', 'warehouse_id', 'status_id', 'payment_status', 'settlement_mode', 'is_completed'},
    'purchase.PurchaseOrder': {'supplier_id', 'warehouse_id', 'status_id', 'payment_status', 'settlement_mode', 'receipt_state', 'is_received'},
    'warehouse.WarehouseTransfer': {'source_warehouse_id', 'destination_warehouse_id', 'status', 'is_completed'},
    'warehouse.StockAdjustment': {'warehouse_id', 'status', 'reason'},
    'warehouse.StockCount': {'warehouse_id', 'status', 'count_type'},
    'manufacturing.ProductionOrder': {'bom_id', 'planned_qty', 'accepted_good_qty', 'rejected_qty', 'status'},
    'hrm.EmployeePromotion': {'employee_id', 'new_designation_id', 'new_department_id', 'effective_date'},
    'hrm.SalaryAdvance': {'employee_id', 'requested_amount', 'approved_amount', 'status'},
}


def _json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _is_sensitive(key: str) -> bool:
    lowered = str(key).lower()
    if lowered == 'card_last4':
        return False
    return any(tok in lowered for tok in REDACT_TOKENS) or lowered == 'card'


def _sanitize_changes(changes, model_label=None):
    if not changes:
        return {}
    allowed = AUDIT_FIELDS.get(model_label) if model_label else None
    cleaned = {}
    for field, delta in changes.items():
        if allowed is not None and field not in allowed:
            continue
        if _is_sensitive(field):
            cleaned[field] = {'old': '[REDACTED]', 'new': '[REDACTED]'}
        elif isinstance(delta, dict) and ('old' in delta or 'new' in delta):
            cleaned[field] = {
                'old': _json_safe(delta.get('old')),
                'new': _json_safe(delta.get('new')),
            }
        else:
            cleaned[field] = _json_safe(delta)
    return cleaned


def _client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()[:45]
    return request.META.get('REMOTE_ADDR')


def _infer_warehouse(instance):
    if instance is None:
        return None
    if instance._meta.model_name == 'warehouse' and instance._meta.app_label == 'warehouse':
        return instance
    warehouse = getattr(instance, 'warehouse', None)
    if warehouse is not None:
        return warehouse
    for parent_attr in ('sales_order', 'purchase_order'):
        parent = getattr(instance, parent_attr, None)
        if parent is not None and getattr(parent, 'warehouse', None) is not None:
            return parent.warehouse
    return None


def log_audit_event(
    actor,
    action,
    instance=None,
    changes=None,
    request=None,
    module=None,
    request_key=None,
    warehouse=None,
    object_repr=None,
):
    if actor is not None and getattr(actor, 'is_authenticated', True) is False:
        actor = None
    model_label = instance._meta.label if instance is not None else ''
    model_name = instance._meta.model_name if instance is not None else 'system'
    object_id = str(instance.pk) if instance is not None and instance.pk is not None else ''
    resolved_module = module or (
        instance._meta.app_label if instance is not None and instance._meta.app_label in AuditLog.Module.values
        else AuditLog.Module.SETTINGS
    )

    if request_key is not None:
        if not isinstance(request_key, UUID):
            request_key = UUID(str(request_key))
        if AuditLog.objects.filter(
            request_key=request_key,
            action=action,
            model_name=model_name,
            object_id=object_id,
        ).exists():
            return None

    resolved_warehouse = warehouse if warehouse is not None else _infer_warehouse(instance)
    user_agent = (request.META.get('HTTP_USER_AGENT', '')[:255]) if request is not None else ''
    repr_text = object_repr if object_repr is not None else (str(instance)[:255] if instance is not None else '')

    return AuditLog.objects.create(
        actor=actor,
        action=action,
        module=resolved_module,
        model_name=model_name,
        object_id=object_id,
        object_repr=repr_text,
        warehouse=resolved_warehouse,
        changes=_sanitize_changes(changes, model_label=model_label),
        request_key=request_key,
        ip_address=_client_ip(request),
        user_agent=user_agent,
    )
