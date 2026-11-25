from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext
from uuid import uuid4

from django.db import connections, transaction
from django.db.models import DecimalField, F, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from common.audit import log_audit_event
from common.idempotency import IdempotentResult, run_idempotent
from common.models import AuditLog, PaymentStatus, SettlementMode
from common.money import convert_to_base, q_money
from people.models import Party
from sale.models import MONEY_PRECISION
from warehouse.scope import lock_warehouse_scope, scoped_queryset

from .models import (
    MAX_AMOUNT, MIN_AMOUNT, ZERO, AccountTransaction, AccountTransfer, Expense, FinancialAccount,
    Income, PartySettlement, PaymentMethod, PaymentOrigin, PurchasePayment, SalesPayment, TransferType,
    base_currency,
)


ACCOUNT_VIEW = 'accounting.view_financialaccount'
EXPENSE_VIEW = 'accounting.view_expense'
STATEMENT_VIEW = (ACCOUNT_VIEW, 'accounting.view_accounttransaction')
TRANSFER_VIEW = (ACCOUNT_VIEW, 'accounting.view_accounttransfer')
PAYMENT_VIEW = (EXPENSE_VIEW, ACCOUNT_VIEW, 'accounting.view_expensepayment')
SALES_PAYMENT_VIEW = ('sale.view_salesorder', ACCOUNT_VIEW, 'accounting.view_salespayment')
PURCHASE_PAYMENT_VIEW = ('purchase.view_purchaseorder', ACCOUNT_VIEW, 'accounting.view_purchasepayment')
INCOME_PAYMENT_VIEW = ('accounting.view_income', ACCOUNT_VIEW, 'accounting.view_incomepayment')
EXPENSE_COMPLETE = ('accounting.change_expense',)

STORED_VALUE_METHODS = (PaymentMethod.GIFT_CARD, PaymentMethod.DEPOSIT)


class ExpenseCompleted(APIException):
    status_code = 409
    default_detail = 'Completed expenses are read-only and cannot be changed or deleted.'
    default_code = 'expense_completed'


class IncomeCompleted(ExpenseCompleted):
    default_detail = 'Completed income is read-only and cannot be changed or deleted.'
    default_code = 'income_completed'


@dataclass(frozen=True)
class OrderBalance:
    recognized_total: Decimal
    return_credits: Decimal
    payments_applied: Decimal
    refunds_issued: Decimal
    net_billable: Decimal
    net_paid: Decimal
    open_due: Decimal
    payment_status: str


def accounts_with_balances(queryset):
    balances = AccountTransaction.objects.filter(account_id=OuterRef('pk')).values('balance_after')[:1]
    return queryset.annotate(balance=Coalesce(Subquery(balances), F('opening_balance'), output_field=DecimalField(
        max_digits=15, decimal_places=4,
    )))


def transfer_queryset(queryset, scope):
    return scoped_queryset(scoped_queryset(queryset, scope, 'source_account__warehouse_id'),
                           scope, 'destination_account__warehouse_id')


def paid_amount(record):
    income = isinstance(record, Income)
    if hasattr(record, 'ledger_payments'):
        postings = ((entry.direction, entry.amount) for payment in record.ledger_payments for entry in payment.ledger_rows)
    else:
        source = 'income_payment__income' if income else 'expense_payment__expense'
        postings = AccountTransaction.objects.filter(**{source: record}).order_by().values_list(
            'direction', 'amount',
        ).iterator()
    paid_direction = AccountTransaction.Direction.INFLOW if income else AccountTransaction.Direction.OUTFLOW
    return sum((amount if direction == paid_direction else -amount for direction, amount in postings), ZERO)


def sales_payment_totals(sales_order):
    bal = compute_order_balance(sales_order)
    return bal.recognized_total, bal.payments_applied, bal.refunds_issued


def return_refund_amount(order, quantities, previous):
    # Round cumulative values so partial returns reconcile to the original merchandise totals (D-22).
    before = after = ZERO
    for line in order.items.filter(pk__in=quantities.keys() | previous.keys()):
        returned = previous.get(line.pk, 0)
        line_total = line.returnable_total
        before += line_total * returned / line.quantity
        after += line_total * (returned + quantities.get(line.pk, 0)) / line.quantity
    rate = order.exchange_rate
    return ((after * rate).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
            - (before * rate).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP))


def _completed_returns_credit_base(order, *, as_of=None):
    is_sale = order._meta.model_name == 'salesorder'
    ret_qs = order.returns.filter(is_completed=True)
    if as_of is not None:
        ret_qs = ret_qs.filter(return_date__lte=as_of)
    ret_qs = ret_qs.order_by('return_date', 'pk')
    item_field = 'sales_order_item_id' if is_sale else 'purchase_order_item_id'
    total = ZERO
    previous = defaultdict(Decimal)
    for ret in ret_qs:
        quantities = dict(ret.items.values_list(item_field, 'quantity'))
        if not quantities:
            continue
        total += return_refund_amount(order, quantities, previous)
        for line_id, qty in quantities.items():
            previous[line_id] += qty
    return total


def _is_order_posted(order):
    if order._meta.model_name == 'salesorder':
        return bool(order.is_completed)
    receipt_state = getattr(order, 'receipt_state', None)
    return bool(order.is_received or receipt_state in ('partial', 'received'))


def compute_order_balance(order, *, as_of=None) -> OrderBalance:
    if not _is_order_posted(order):
        return OrderBalance(
            recognized_total=ZERO, return_credits=ZERO, payments_applied=ZERO, refunds_issued=ZERO,
            net_billable=ZERO, net_paid=ZERO, open_due=ZERO, payment_status=PaymentStatus.PENDING,
        )

    if order.recognized_total_base is not None:
        recognized_total = Decimal(order.recognized_total_base).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
    else:
        recognized_total = convert_to_base(order.grand_total, order.exchange_rate, digits=4)

    return_credits = _completed_returns_credit_base(order, as_of=as_of)
    is_sale = order._meta.model_name == 'salesorder'
    ret_fk = 'sales_return_id' if is_sale else 'purchase_return_id'
    payments = order.payments.all()
    if as_of is not None:
        payments = payments.filter(date__lte=as_of)

    payments_applied = ZERO
    refunds_issued = ZERO
    for ret_id, is_rev, amount in payments.values_list(ret_fk, 'is_reversal', 'amount').iterator():
        signed = -amount if is_rev else amount
        if ret_id is None:
            payments_applied += signed
        else:
            refunds_issued += signed

    net_billable = recognized_total - return_credits
    net_paid = payments_applied - refunds_issued
    open_due = net_billable - net_paid

    if open_due <= ZERO:
        pstatus = PaymentStatus.PAID
    elif net_paid <= ZERO:
        pstatus = PaymentStatus.DUE
    else:
        pstatus = PaymentStatus.PARTIAL

    return OrderBalance(
        recognized_total=recognized_total,
        return_credits=return_credits,
        payments_applied=payments_applied,
        refunds_issued=refunds_issued,
        net_billable=net_billable,
        net_paid=net_paid,
        open_due=open_due,
        payment_status=pstatus,
    )


def refresh_payment_status(order, *, save=True):
    bal = compute_order_balance(order)
    if save and order.payment_status != bal.payment_status:
        type(order).objects.filter(pk=order.pk).update(payment_status=bal.payment_status)
    order.payment_status = bal.payment_status
    return bal


def lock_rows(queryset):
    # SQLite needs to acquire its write lock before reading balances or payment totals.
    if not connections[queryset.db].features.has_select_for_update:
        queryset.update(updated_at=F('updated_at'))
    return queryset.select_for_update().order_by('pk')


def lock_income_expense(model, pk, scope):
    return get_object_or_404(lock_rows(scoped_queryset(model.objects.filter(pk=pk), scope)))


def lock_financial_scope(actor, *permissions):
    scope = lock_warehouse_scope(actor)
    if not has_role_permissions(actor, permissions):
        raise PermissionDenied('You do not have permission to perform this accounting action.')
    return scope


def locked_accounts(scope, **account_ids):
    accounts = scoped_queryset(FinancialAccount.objects.filter(pk__in=account_ids.values()), scope)
    found = {account.pk: account for account in lock_rows(accounts)}
    if any(pk not in found for pk in account_ids.values()):
        raise ValidationError({field: 'Choose an account within your warehouse access.'
                               for field, pk in account_ids.items() if pk not in found})
    return {field: found[pk] for field, pk in account_ids.items()}


def check_active_account(account, field):
    if not account.is_active:
        raise ValidationError({field: 'Choose an active account.'})


def available_payment_accounts(scope):
    accounts = FinancialAccount.objects.filter(is_active=True, currency=base_currency())
    return scoped_queryset(accounts, scope)


def payment_account_options(accounts):
    return [{'value': account.pk, 'label': account.name, 'warehouse': account.warehouse_id} for account in accounts]


def payment_account_error(account, warehouse_id):
    if not account.is_active:
        return 'Choose an active payment account.'
    if account.warehouse_id is not None and account.warehouse_id != warehouse_id:
        return 'Choose a payment account for this warehouse or a shared account.'
    if account.currency != base_currency():
        return 'Choose a payment account in the base currency.'
    return None


def lock_payment_account(scope, account_id, warehouse_id):
    if account_id is None:
        raise ValidationError({'payment_account': 'Choose a payment account.'})
    account = locked_accounts(scope, payment_account=account_id)['payment_account']
    error = payment_account_error(account, warehouse_id)
    if error:
        raise ValidationError({'payment_account': error})
    return account


def audit_fields(actor):
    username = actor.get_username() if actor is not None else ''
    return {'create_by': username, 'modified_by': username}


def post_transaction(account, direction, actor, *, date, amount, transfer=None, expense_payment=None, income_payment=None,
                     sales_payment=None, purchase_payment=None, source_kind=None, source_ref_id=None, source_reference=''):
    if sales_payment:
        expected_direction = (AccountTransaction.Direction.OUTFLOW if (sales_payment.sales_return_id or sales_payment.is_reversal)
                              else AccountTransaction.Direction.INFLOW)
        if direction != expected_direction:
            raise ValidationError({'direction': 'The transaction direction must match its sale payment or refund.'})
        source_kind = AccountTransaction.SourceKind.SALES_PAYMENT
    elif purchase_payment:
        expected_direction = (AccountTransaction.Direction.INFLOW if (purchase_payment.purchase_return_id or purchase_payment.is_reversal)
                              else AccountTransaction.Direction.OUTFLOW)
        if direction != expected_direction:
            raise ValidationError({'direction': 'The transaction direction must match its purchase payment or refund.'})
        source_kind = AccountTransaction.SourceKind.PURCHASE_PAYMENT
    elif transfer:
        source_kind = AccountTransaction.SourceKind.ACCOUNT_TRANSFER
    elif expense_payment:
        source_kind = AccountTransaction.SourceKind.EXPENSE_PAYMENT
    elif income_payment:
        source_kind = AccountTransaction.SourceKind.INCOME_PAYMENT
    else:
        if source_kind not in (
            AccountTransaction.SourceKind.CUSTOMER_DEPOSIT,
            AccountTransaction.SourceKind.GIFT_CARD_SALE,
            AccountTransaction.SourceKind.REGISTER_MOVEMENT,
            AccountTransaction.SourceKind.SALARY_ADVANCE,
            AccountTransaction.SourceKind.SALARY_PAYMENT,
        ) or source_ref_id is None:
            raise ValidationError({'source_kind': 'Provide a valid transaction source.'})

    last_balance = account.transactions.values_list('balance_after', flat=True).first()
    balance = account.opening_balance if last_balance is None else last_balance
    balance += amount if direction == AccountTransaction.Direction.INFLOW else -amount
    if balance < ZERO:
        raise ValidationError({'amount': 'The account has insufficient funds.'})
    if balance > MAX_AMOUNT:
        raise ValidationError({'amount': 'The resulting account balance is too large.'})
    return AccountTransaction.objects.create(
        account=account, direction=direction, date=date, amount=amount, balance_after=balance,
        source_kind=source_kind, source_ref_id=source_ref_id, source_reference=source_reference,
        transfer=transfer, expense_payment=expense_payment, income_payment=income_payment,
        sales_payment=sales_payment, purchase_payment=purchase_payment, **audit_fields(actor),
    )


def create_source(model, actor, request_key, **payload):
    record, created = model.objects.get_or_create(request_key=request_key, defaults={**payload, **audit_fields(actor)})
    if not created and any(getattr(record, field) != value for field, value in payload.items()):
        raise ValidationError({'request_key': 'This request key has already been used for different details.'})
    return record, created


@transaction.atomic
def post_transfer(actor, *, source_account, destination_account, amount, date, reference, request_key,
                  transfer_type=TransferType.FUND_TRANSFER):
    scope = lock_financial_scope(actor, *TRANSFER_VIEW, 'accounting.add_accounttransfer')
    if source_account == destination_account:
        raise ValidationError({'destination_account': 'Choose a different destination account.'})
    accounts = locked_accounts(scope, source_account=source_account, destination_account=destination_account)
    source, destination = accounts['source_account'], accounts['destination_account']
    record, created = create_source(AccountTransfer, actor, request_key, source_account_id=source.pk,
                                    destination_account_id=destination.pk, amount=amount, date=date, reference=reference,
                                    transfer_type=transfer_type)
    if created:
        for field, account in accounts.items():
            check_active_account(account, field)
        if source.currency != destination.currency:
            raise ValidationError({'destination_account': 'Both accounts must use the same currency.'})
        post_transaction(source, AccountTransaction.Direction.OUTFLOW, actor, date=date, amount=amount, transfer=record)
        post_transaction(destination, AccountTransaction.Direction.INFLOW, actor, date=date, amount=amount, transfer=record)
    return record, created


@transaction.atomic
def complete_income_expense(actor, model, pk):
    key = model._meta.model_name
    scope = lock_financial_scope(actor, f'accounting.change_{key}')
    record = lock_income_expense(model, pk, scope)
    if record.is_completed:
        return record, False
    account = lock_payment_account(scope, record.payment_account_id, record.warehouse_id)
    now = timezone.now()
    due = record.amount - paid_amount(record)
    if due > ZERO:
        date = timezone.localdate(now)
        payment = record.payments.create(
            account=account, amount=due, date=date, reference=f'{key.capitalize()} #{record.pk}',
            method=account.type, **audit_fields(actor),
        )
        direction = AccountTransaction.Direction.INFLOW if model is Income else AccountTransaction.Direction.OUTFLOW
        post_transaction(account, direction, actor, date=date, amount=due, **{f'{key}_payment': payment})
    record.is_completed = True
    record.completed_at = now
    record.modified_by = actor.get_username()
    record.save(update_fields=['is_completed', 'completed_at', 'modified_by', 'updated_at'])
    return record, True


def _stored_value_hooks():
    # PRICE-01 attaches real gift card / deposit handlers; return None until installed.
    try:
        from sale import loyalty as sale_loyalty
        return sale_loyalty
    except ImportError:
        return None


def _validate_and_normalize_tenders(tenders, *, allow_stored_value=True):
    if not tenders:
        raise ValidationError({'tenders': 'Provide at least one tender line.'})
    normalized = []
    for idx, tender in enumerate(tenders):
        method = tender.get('method')
        if method not in PaymentMethod.values:
            raise ValidationError({'tenders': f'Tender {idx + 1}: invalid payment method.'})
        if not allow_stored_value and method in STORED_VALUE_METHODS:
            raise ValidationError({'tenders': f'Tender {idx + 1}: {method} is only supported for customer sales.'})
        try:
            amount_raw = Decimal(str(tender.get('amount', '0')))
        except Exception as exc:
            raise ValidationError({'tenders': f'Tender {idx + 1}: invalid amount.'}) from exc
        amount_display = q_money(amount_raw)
        if amount_display <= ZERO:
            raise ValidationError({'tenders': f'Tender {idx + 1}: payment amount must be positive after rounding.'})
        amount = amount_raw.quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
        if amount <= ZERO:
            raise ValidationError({'tenders': f'Tender {idx + 1}: payment amount must be positive.'})
        account_id = tender.get('account_id', tender.get('account'))
        if method in STORED_VALUE_METHODS:
            account_id = None
        elif account_id is None:
            raise ValidationError({'tenders': f'Tender {idx + 1}: choose an account.'})
        normalized.append({
            'method': method,
            'account_id': account_id,
            'amount': amount,
            'cheque_number': str(tender.get('cheque_number') or ''),
            'card_last4': str(tender.get('card_last4') or '')[:4],
            'gift_card_id': tender.get('gift_card_id'),
            'note': str(tender.get('note') or ''),
        })
    return normalized


def _post_order_payment_impl(
    *, actor, is_sale, order_id, tenders, payment_date, request_key,
    route_excess_to_deposit=False, origin=PaymentOrigin.MANUAL, settlement=None,
    register_session_id=None, bypass_perm_check=False,
):
    from purchase.models import PurchaseOrder
    from sale.models import SalesOrder

    order_model = SalesOrder if is_sale else PurchaseOrder
    perm = ('accounting.add_sales_payment',) if is_sale else ('accounting.add_purchase_payment',)
    scope_name = 'sales_payment' if is_sale else 'purchase_payment'
    payload = {
        'order_id': order_id,
        'payment_date': str(payment_date or timezone.localdate()),
        'route_excess_to_deposit': bool(route_excess_to_deposit),
        'origin': origin,
        'tenders': tenders,
    }

    def execute():
        scope = lock_warehouse_scope(actor)
        if not bypass_perm_check and not has_role_permissions(actor, perm):
            raise PermissionDenied('You do not have permission to record this payment.')

        # Lock order (1)
        orders_qs = scoped_queryset(order_model.objects.all(), scope)
        if not connections[order_model.objects.db].features.has_select_for_update:
            orders_qs.filter(pk=order_id).update(updated_at=F('updated_at'))
        order = orders_qs.select_for_update().filter(pk=order_id).first()
        if order is None:
            raise NotFound('Order not found.')
        if not _is_order_posted(order):
            raise ValidationError({'order': 'Payments are accepted only on posted orders.'})

        norm_tenders = _validate_and_normalize_tenders(tenders, allow_stored_value=is_sale)
        pay_date = payment_date or timezone.localdate()

        # Lock party (2) if deposit used or routing excess
        uses_deposit = any(t['method'] == PaymentMethod.DEPOSIT for t in norm_tenders) or route_excess_to_deposit
        party = None
        if uses_deposit:
            if not order.customer_id:
                raise ValidationError({'customer': 'A customer is required for deposit operations.'})
            party = Party.all_objects.select_for_update().filter(pk=order.customer_id).first()

        # Lock accounts (5) in ascending PK order
        account_ids = sorted({t['account_id'] for t in norm_tenders if t['account_id'] is not None})
        locked_accts = {}
        for acct_id in account_ids:
            locked_accts[acct_id] = lock_payment_account(scope, acct_id, order.warehouse_id)

        bal = compute_order_balance(order)
        total_tender = sum((t['amount'] for t in norm_tenders), ZERO)
        if total_tender <= ZERO or total_tender > MAX_AMOUNT:
            raise ValidationError({'amount': 'Invalid payment total.'})

        excess = ZERO
        if total_tender > bal.open_due:
            if is_sale and route_excess_to_deposit:
                excess = total_tender - bal.open_due
                if bal.open_due <= ZERO and excess == total_tender:
                    pass
            else:
                raise ValidationError({'amount': f'Payment total ({total_tender}) exceeds open due ({bal.open_due}).'})

        hooks = _stored_value_hooks()
        payment_ids = []
        applied_left = min(total_tender, max(bal.open_due, ZERO))
        created_payments = []

        for idx, t in enumerate(norm_tenders):
            acct = locked_accts.get(t['account_id'])
            # Allocate invoice amount vs excess (for route_excess_to_deposit)
            invoice_part = min(t['amount'], applied_left)
            applied_left -= invoice_part
            deposit_part = t['amount'] - invoice_part

            if invoice_part > ZERO:
                row_key = uuid4() if len(norm_tenders) > 1 or deposit_part > ZERO else request_key
                # Use deterministic sub-key only on single-line non-split; IdempotencyRecord holds the command key.
                if is_sale:
                    kwargs = dict(
                        sales_order=order, account=acct, date=pay_date, amount=invoice_part,
                        method=t['method'], reference=order.order_number, request_key=row_key,
                        cheque_number=t['cheque_number'], card_last4=t['card_last4'],
                        gift_card_id=t['gift_card_id'], note=t['note'], origin=origin,
                        settlement=settlement, **audit_fields(actor),
                    )
                    if hasattr(SalesPayment, 'register_session') and register_session_id is not None:
                        kwargs['register_session_id'] = register_session_id
                    payment = SalesPayment.objects.create(**kwargs)
                    if t['method'] == PaymentMethod.GIFT_CARD:
                        if hooks is None or not hasattr(hooks, 'redeem_gift_card'):
                            raise ValidationError({'tenders': 'Gift card redemption is not enabled yet.'})
                        hooks.redeem_gift_card(t['gift_card_id'], invoice_part, payment.pk, uuid4(), actor=actor)
                    elif t['method'] == PaymentMethod.DEPOSIT:
                        if hooks is None or not hasattr(hooks, 'debit_deposit'):
                            raise ValidationError({'tenders': 'Customer deposit redemption is not enabled yet.'})
                        hooks.debit_deposit(party.pk, invoice_part, payment.pk, uuid4(), actor=actor)
                    else:
                        post_transaction(
                            acct, AccountTransaction.Direction.INFLOW, actor,
                            date=pay_date, amount=invoice_part, sales_payment=payment,
                        )
                else:
                    payment = PurchasePayment.objects.create(
                        purchase_order=order, account=acct, date=pay_date, amount=invoice_part,
                        method=t['method'], reference=order.order_number, request_key=row_key,
                        cheque_number=t['cheque_number'], card_last4=t['card_last4'],
                        note=t['note'], origin=origin, settlement=settlement, **audit_fields(actor),
                    )
                    post_transaction(
                        acct, AccountTransaction.Direction.OUTFLOW, actor,
                        date=pay_date, amount=invoice_part, purchase_payment=payment,
                    )
                payment_ids.append(payment.pk)
                created_payments.append(payment)

            if deposit_part > ZERO:
                if hooks is None or not hasattr(hooks, 'add_deposit_from_excess'):
                    raise ValidationError({'amount': 'Deposit routing is not enabled yet.'})
                hooks.add_deposit_from_excess(
                    actor, party.pk, deposit_part, acct.pk if acct else None,
                    f'Excess payment on {order.order_number}', uuid4(),
                )

        updated = refresh_payment_status(order)
        if hasattr(hooks, 'maybe_earn_points_for_order') and is_sale and updated.payment_status == PaymentStatus.PAID:
            hooks.maybe_earn_points_for_order(order, actor=actor)

        log_audit_event(
            actor, AuditLog.Action.PAYMENT, instance=order,
            changes={'payment_ids': payment_ids, 'open_due': str(updated.open_due), 'payment_status': updated.payment_status},
            module=AuditLog.Module.ACCOUNTING, request_key=request_key,
        )
        result = {
            'payment_ids': payment_ids,
            'payment_status': updated.payment_status,
            'paid': format(updated.net_paid, '.4f'),
            'returned': format(updated.return_credits, '.4f'),
            'due': format(updated.open_due, '.4f'),
        }
        return IdempotentResult(scope_name, order.pk, result, result)

    result, _ = run_idempotent(request_key, scope_name, payload, execute)
    return result


def post_sales_payment(
    actor, sales_order_id, tenders, payment_date, request_key,
    route_excess_to_deposit=False, *, origin=PaymentOrigin.MANUAL, settlement=None,
    register_session_id=None, bypass_perm_check=False,
):
    return _post_order_payment_impl(
        actor=actor, is_sale=True, order_id=sales_order_id, tenders=tenders,
        payment_date=payment_date, request_key=request_key,
        route_excess_to_deposit=route_excess_to_deposit, origin=origin,
        settlement=settlement, register_session_id=register_session_id,
        bypass_perm_check=bypass_perm_check,
    )


def post_purchase_payment(
    actor, purchase_order_id, tenders, payment_date, request_key,
    *, origin=PaymentOrigin.MANUAL, settlement=None, bypass_perm_check=False,
):
    return _post_order_payment_impl(
        actor=actor, is_sale=False, order_id=purchase_order_id, tenders=tenders,
        payment_date=payment_date, request_key=request_key,
        route_excess_to_deposit=False, origin=origin, settlement=settlement,
        bypass_perm_check=bypass_perm_check,
    )


def reverse_payment(actor, payment_id, request_key, reason, *, kind=None):
    payload = {'payment_id': payment_id, 'kind': kind, 'reason': reason}

    def execute():
        scope = lock_warehouse_scope(actor)
        if not has_role_permissions(actor, ('accounting.reverse_payment',)):
            raise PermissionDenied('You do not have permission to reverse payments.')

        if kind == 'sales':
            model = SalesPayment
            order_lookup = 'sales_order__warehouse_id'
        elif kind == 'purchase':
            model = PurchasePayment
            order_lookup = 'purchase_order__warehouse_id'
        else:
            s_exists = scoped_queryset(SalesPayment.objects.filter(pk=payment_id), scope, 'sales_order__warehouse_id').exists()
            p_exists = scoped_queryset(PurchasePayment.objects.filter(pk=payment_id), scope, 'purchase_order__warehouse_id').exists()
            if s_exists and p_exists:
                raise ValidationError({'kind': 'Specify payment kind ("sales" or "purchase").'})
            if p_exists:
                model = PurchasePayment
                order_lookup = 'purchase_order__warehouse_id'
            else:
                model = SalesPayment
                order_lookup = 'sales_order__warehouse_id'

        is_sale = model is SalesPayment
        qs = scoped_queryset(model.objects.all(), scope, order_lookup)
        if not connections[model.objects.db].features.has_select_for_update:
            qs.filter(pk=payment_id).update(updated_at=F('updated_at'))
        orig = qs.select_for_update().filter(pk=payment_id).first()
        if orig is None:
            raise NotFound('Payment not found.')

        if orig.is_reversal:
            raise ValidationError({'payment': 'A reversal payment cannot itself be reversed.'})
        if (orig.sales_return_id if is_sale else orig.purchase_return_id) is not None:
            raise ValidationError({'payment': 'A refund payment cannot be reversed.'})
        if orig.reversals.exists():
            raise ValidationError({'payment': 'This payment has already been reversed.'})

        order = orig.sales_order if is_sale else orig.purchase_order
        acct = lock_payment_account(scope, orig.account_id, order.warehouse_id) if orig.account_id else None
        today = timezone.localdate()
        if is_sale:
            rev = SalesPayment.objects.create(
                sales_order=order, account=acct, date=today, amount=orig.amount,
                method=orig.method, reference=f'REV-{orig.number}', request_key=uuid4(),
                note=reason, is_reversal=True, reversed_payment=orig, origin=orig.origin,
                gift_card=orig.gift_card, **audit_fields(actor),
            )
            hooks = _stored_value_hooks()
            if orig.method == PaymentMethod.GIFT_CARD and hooks and hasattr(hooks, 'reverse_gift_card_redemption'):
                hooks.reverse_gift_card_redemption(orig.gift_card_id, orig.amount, rev.pk, uuid4(), actor=actor)
            elif orig.method == PaymentMethod.DEPOSIT and hooks and hasattr(hooks, 'credit_deposit_reversal'):
                hooks.credit_deposit_reversal(order.customer_id, orig.amount, rev.pk, uuid4(), actor=actor)
            else:
                post_transaction(acct, AccountTransaction.Direction.OUTFLOW, actor, date=today, amount=orig.amount, sales_payment=rev)
        else:
            rev = PurchasePayment.objects.create(
                purchase_order=order, account=acct, date=today, amount=orig.amount,
                method=orig.method, reference=f'REV-{orig.number}', request_key=uuid4(),
                note=reason, is_reversal=True, reversed_payment=orig, origin=orig.origin,
                **audit_fields(actor),
            )
            post_transaction(acct, AccountTransaction.Direction.INFLOW, actor, date=today, amount=orig.amount, purchase_payment=rev)

        bal = refresh_payment_status(order)
        log_audit_event(
            actor, AuditLog.Action.REFUND, instance=rev,
            changes={'reversed_payment_id': orig.pk, 'reason': reason},
            module=AuditLog.Module.ACCOUNTING, request_key=request_key, warehouse=order.warehouse,
        )
        res = {
            'reversal_id': rev.pk,
            'payment_status': bal.payment_status,
            'due': format(bal.open_due, '.4f'),
        }
        return IdempotentResult('payment_reversal', rev.pk, res, res)

    result, _ = run_idempotent(request_key, f'reverse_payment:{kind or "auto"}', payload, execute)
    return result


def post_completed_sale_payment(sales_order, scope, actor, *, date):
    """Called while the sale completion transaction holds the order lock (legacy auto_full)."""
    account = lock_payment_account(scope, sales_order.payment_account_id, sales_order.warehouse_id)
    bal = compute_order_balance(sales_order)
    due = bal.open_due
    if due <= ZERO:
        return
    if due > MAX_AMOUNT:
        raise ValidationError({'amount': 'The outstanding sale amount exceeds the ledger limit.'})
    payment = SalesPayment.objects.create(
        sales_order=sales_order, account=account, amount=due, date=date, reference=sales_order.order_number,
        method=account.type, origin=PaymentOrigin.LEGACY_AUTO, **audit_fields(actor),
    )
    post_transaction(account, AccountTransaction.Direction.INFLOW, actor, date=date, amount=due,
                     sales_payment=payment)


def post_sales_return_refund(record, scope, actor, *, date, amount):
    """Reconcile return credit and LIFO cash/stored-value refund while the sales order is locked (D-05)."""
    order = record.sales_order
    # Compute open_due_before excluding the just-marked-completed current return.
    bal_before = compute_order_balance(order)
    # Since record is already marked is_completed=True inside this transaction, add back `amount` to get before-return due.
    open_due_before = max(bal_before.open_due + amount, ZERO)
    net_paid = max(bal_before.net_paid, ZERO)

    if order.settlement_mode == SettlementMode.AUTO_FULL:
        # Legacy compatibility: auto_full refunds directly against net collections up to R.
        credit_offset = max(amount - net_paid, ZERO)
        cash_refund = min(amount, net_paid)
    else:
        credit_offset = min(amount, open_due_before)
        cash_refund = min(amount - credit_offset, net_paid)

    if cash_refund <= ZERO:
        refresh_payment_status(order)
        return {'credit_offset': credit_offset, 'refunds': []}

    # Allocate LIFO across original non-reversal sales payments that still have unrefunded net balance.
    originals = list(
        SalesPayment.objects.filter(sales_order_id=order.pk, sales_return__isnull=True, is_reversal=False)
        .exclude(reversals__isnull=False)
        .order_by('-date', '-pk')
    )
    refunded_by_orig = defaultdict(lambda: ZERO)
    for orig_id, amt in SalesPayment.objects.filter(
        sales_order_id=order.pk, sales_return__isnull=False, is_reversal=False, refund_of__isnull=False,
    ).values_list('refund_of_id', 'amount'):
        refunded_by_orig[orig_id] += amt

    # Legacy payments without refund_of tracking fallback
    if not originals:
        refresh_payment_status(order)
        return {'credit_offset': credit_offset, 'refunds': []}

    hooks = _stored_value_hooks()
    remaining = cash_refund
    refund_rows = []

    # First check total unallocated refunds (for legacy refunds created without refund_of)
    unlinked_refunded = sum(
        SalesPayment.objects.filter(
            sales_order_id=order.pk, sales_return__isnull=False, is_reversal=False, refund_of__isnull=True,
        ).values_list('amount', flat=True),
        ZERO,
    )

    for orig in originals:
        if remaining <= ZERO:
            break
        already = refunded_by_orig[orig.pk]
        if unlinked_refunded > ZERO:
            ded = min(unlinked_refunded, max(orig.amount - already, ZERO))
            already += ded
            unlinked_refunded -= ded
        available = orig.amount - already
        if available <= ZERO:
            continue
        refund = min(remaining, available)
        if refund > MAX_AMOUNT:
            raise ValidationError({'amount': 'The sales refund exceeds the ledger limit.'})

        if orig.method == PaymentMethod.GIFT_CARD:
            payment = SalesPayment.objects.create(
                sales_order_id=order.pk, sales_return=record, account=None, amount=refund,
                date=date, reference=record.return_number, method=PaymentMethod.GIFT_CARD,
                gift_card=orig.gift_card, refund_of=orig, origin=PaymentOrigin.REFUND, **audit_fields(actor),
            )
            if hooks and hasattr(hooks, 'reverse_gift_card_redemption'):
                hooks.reverse_gift_card_redemption(orig.gift_card_id, refund, payment.pk, uuid4(), actor=actor)
        elif orig.method == PaymentMethod.DEPOSIT:
            payment = SalesPayment.objects.create(
                sales_order_id=order.pk, sales_return=record, account=None, amount=refund,
                date=date, reference=record.return_number, method=PaymentMethod.DEPOSIT,
                refund_of=orig, origin=PaymentOrigin.REFUND, **audit_fields(actor),
            )
            if hooks and hasattr(hooks, 'credit_deposit_refund'):
                hooks.credit_deposit_refund(order.customer_id, refund, payment.pk, uuid4(), actor=actor)
        else:
            account = lock_payment_account(scope, orig.account_id, order.warehouse_id)
            payment = SalesPayment.objects.create(
                sales_order_id=order.pk, sales_return=record, account=account, amount=refund,
                date=date, reference=record.return_number, method=orig.method,
                refund_of=orig, origin=PaymentOrigin.REFUND, **audit_fields(actor),
            )
            post_transaction(account, AccountTransaction.Direction.OUTFLOW, actor, date=date, amount=refund,
                             sales_payment=payment)

        refund_rows.append(payment)
        remaining -= refund

    refresh_payment_status(order)
    return {'credit_offset': credit_offset, 'refunds': refund_rows}


def post_received_purchase_payment(purchase_order, scope, actor, *, date):
    """Called inside the transaction that receives and locks the purchase (legacy auto_full)."""
    account = lock_payment_account(scope, purchase_order.payment_account_id, purchase_order.warehouse_id)
    bal = compute_order_balance(purchase_order)
    amount = bal.open_due
    if amount <= ZERO:
        return
    if amount > MAX_AMOUNT:
        raise ValidationError({'amount': 'The purchase payment exceeds the ledger limit.'})
    payment = PurchasePayment.objects.create(
        purchase_order=purchase_order, account=account, amount=amount, date=date,
        reference=purchase_order.order_number, method=account.type,
        origin=PaymentOrigin.LEGACY_AUTO, **audit_fields(actor),
    )
    post_transaction(account, AccountTransaction.Direction.OUTFLOW, actor, date=date, amount=amount,
                     purchase_payment=payment)


def post_purchase_return_refund(record, scope, actor, *, date, amount):
    """Reconcile return credit and LIFO cash refund while the purchase order is locked (D-05)."""
    order = record.purchase_order
    bal_before = compute_order_balance(order)
    open_due_before = max(bal_before.open_due + amount, ZERO)
    net_paid = max(bal_before.net_paid, ZERO)

    if order.settlement_mode == SettlementMode.AUTO_FULL:
        credit_offset = max(amount - net_paid, ZERO)
        cash_refund = min(amount, net_paid)
    else:
        credit_offset = min(amount, open_due_before)
        cash_refund = min(amount - credit_offset, net_paid)

    if cash_refund <= ZERO:
        refresh_payment_status(order)
        return {'credit_offset': credit_offset, 'refunds': []}

    originals = list(
        PurchasePayment.objects.filter(purchase_order_id=order.pk, purchase_return__isnull=True, is_reversal=False)
        .exclude(reversals__isnull=False)
        .order_by('-date', '-pk')
    )
    refunded_by_orig = defaultdict(lambda: ZERO)
    for orig_id, amt in PurchasePayment.objects.filter(
        purchase_order_id=order.pk, purchase_return__isnull=False, is_reversal=False, refund_of__isnull=False,
    ).values_list('refund_of_id', 'amount'):
        refunded_by_orig[orig_id] += amt

    unlinked_refunded = sum(
        PurchasePayment.objects.filter(
            purchase_order_id=order.pk, purchase_return__isnull=False, is_reversal=False, refund_of__isnull=True,
        ).values_list('amount', flat=True),
        ZERO,
    )

    remaining = cash_refund
    refund_rows = []
    for orig in originals:
        if remaining <= ZERO:
            break
        already = refunded_by_orig[orig.pk]
        if unlinked_refunded > ZERO:
            ded = min(unlinked_refunded, max(orig.amount - already, ZERO))
            already += ded
            unlinked_refunded -= ded
        available = orig.amount - already
        if available <= ZERO:
            continue
        refund = min(remaining, available)
        account = lock_payment_account(scope, orig.account_id, order.warehouse_id)
        payment = PurchasePayment.objects.create(
            purchase_order_id=order.pk, purchase_return=record, account=account, amount=refund,
            date=date, reference=record.return_number, method=orig.method,
            refund_of=orig, origin=PaymentOrigin.REFUND, **audit_fields(actor),
        )
        post_transaction(account, AccountTransaction.Direction.INFLOW, actor, date=date, amount=refund,
                         purchase_payment=payment)
        refund_rows.append(payment)
        remaining -= refund

    refresh_payment_status(order)
    return {'credit_offset': credit_offset, 'refunds': refund_rows}


def party_due_summary(party_id, *, as_of=None, scope=None, warehouse_id=None):
    from purchase.models import PurchaseOrder
    from sale.models import SalesOrder

    party = Party.all_objects.filter(pk=party_id).first()
    if party is None:
        raise NotFound('Party not found.')

    sale_qs = SalesOrder.objects.filter(customer_id=party_id, is_completed=True)
    purchase_qs = PurchaseOrder.objects.filter(supplier_id=party_id).filter(
        Q(is_received=True) | Q(payment_status__in=[PaymentStatus.DUE, PaymentStatus.PARTIAL, PaymentStatus.PAID])
    )
    if scope is not None:
        sale_qs = scoped_queryset(sale_qs, scope)
        purchase_qs = scoped_queryset(purchase_qs, scope)
    if warehouse_id is not None:
        sale_qs = sale_qs.filter(warehouse_id=warehouse_id)
        purchase_qs = purchase_qs.filter(warehouse_id=warehouse_id)
    if as_of is not None:
        sale_qs = sale_qs.filter(order_date__lte=as_of)
        purchase_qs = purchase_qs.filter(order_date__lte=as_of)

    receivables = []
    rec_totals = {'grand_total': ZERO, 'returned': ZERO, 'paid': ZERO, 'due': ZERO}
    for order in sale_qs.order_by('order_date', 'pk'):
        bal = compute_order_balance(order, as_of=as_of)
        row = {
            'id': order.pk,
            'number': order.order_number,
            'date': order.order_date.isoformat(),
            'warehouse_id': order.warehouse_id,
            'grand_total': format(bal.recognized_total, '.4f'),
            'returned': format(bal.return_credits, '.4f'),
            'paid': format(bal.net_paid, '.4f'),
            'due': format(bal.open_due, '.4f'),
            'payment_status': bal.payment_status,
        }
        receivables.append(row)
        rec_totals['grand_total'] += bal.recognized_total
        rec_totals['returned'] += bal.return_credits
        rec_totals['paid'] += bal.net_paid
        rec_totals['due'] += bal.open_due

    payables = []
    pay_totals = {'grand_total': ZERO, 'returned': ZERO, 'paid': ZERO, 'due': ZERO}
    for order in purchase_qs.order_by('order_date', 'pk'):
        bal = compute_order_balance(order, as_of=as_of)
        row = {
            'id': order.pk,
            'number': order.order_number,
            'date': order.order_date.isoformat(),
            'warehouse_id': order.warehouse_id,
            'grand_total': format(bal.recognized_total, '.4f'),
            'returned': format(bal.return_credits, '.4f'),
            'paid': format(bal.net_paid, '.4f'),
            'due': format(bal.open_due, '.4f'),
            'payment_status': bal.payment_status,
        }
        payables.append(row)
        pay_totals['grand_total'] += bal.recognized_total
        pay_totals['returned'] += bal.return_credits
        pay_totals['paid'] += bal.net_paid
        pay_totals['due'] += bal.open_due

    return {
        'party_id': party.pk,
        'party_name': party.name,
        'as_of': as_of.isoformat() if as_of else None,
        'receivable': {
            'documents': receivables,
            'totals': {k: format(v, '.4f') for k, v in rec_totals.items()},
        },
        'payable': {
            'documents': payables,
            'totals': {k: format(v, '.4f') for k, v in pay_totals.items()},
        },
    }


def preview_clear_due(party_id, amount, direction, *, scope=None):
    from purchase.models import PurchaseOrder
    from sale.models import SalesOrder

    party = Party.all_objects.filter(pk=party_id).first()
    if party is None:
        raise NotFound('Party not found.')
    if direction not in ('receipt', 'disbursement'):
        raise ValidationError({'direction': 'Choose receipt or disbursement.'})
    amt = Decimal(str(amount)).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
    if amt <= ZERO:
        raise ValidationError({'amount': 'Enter a positive amount.'})

    if direction == 'receipt':
        qs = SalesOrder.objects.filter(customer_id=party_id, is_completed=True)
    else:
        qs = PurchaseOrder.objects.filter(supplier_id=party_id, is_received=True)
    if scope is not None:
        qs = scoped_queryset(qs, scope)

    allocations = []
    remaining = amt
    for order in qs.order_by('order_date', 'pk'):
        if remaining <= ZERO:
            break
        bal = compute_order_balance(order)
        if bal.open_due <= ZERO:
            continue
        alloc = min(remaining, bal.open_due)
        allocations.append({
            'order_id': order.pk,
            'order_number': order.order_number,
            'order_date': order.order_date.isoformat(),
            'warehouse_id': order.warehouse_id,
            'open_due_before': format(bal.open_due, '.4f'),
            'allocated': format(alloc, '.4f'),
            'open_due_after': format(bal.open_due - alloc, '.4f'),
        })
        remaining -= alloc

    total_allocated = amt - remaining
    if total_allocated <= ZERO:
        raise ValidationError({'amount': 'No open due documents found to settle.'})
    if remaining > ZERO:
        raise ValidationError({'amount': f'Amount ({amt}) exceeds total open due ({total_allocated}).'})

    return {
        'party_id': party.pk,
        'direction': direction,
        'amount': format(amt, '.4f'),
        'allocations': allocations,
    }


def confirm_clear_due(actor, party_id, amount, direction, account_id, method, payment_date, request_key, *, note=''):
    payload = {
        'party_id': party_id,
        'amount': str(amount),
        'direction': direction,
        'account_id': account_id,
        'method': method,
        'payment_date': str(payment_date or timezone.localdate()),
        'note': note,
    }

    def execute():
        scope = lock_warehouse_scope(actor)
        if not has_role_permissions(actor, ('accounting.clear_due',)):
            raise PermissionDenied('You do not have permission to clear party due balances.')
        if method not in PaymentMethod.values or method in STORED_VALUE_METHODS:
            raise ValidationError({'method': 'Choose a cash, bank, card, mobile banking, or cheque method.'})

        Party.all_objects.select_for_update().filter(pk=party_id).first()
        preview = preview_clear_due(party_id, amount, direction, scope=scope)
        acct = locked_accounts(scope, account=account_id)['account']
        check_active_account(acct, 'account')
        pay_date = payment_date or timezone.localdate()
        amt = Decimal(preview['amount'])

        settlement = PartySettlement.objects.create(
            party_id=party_id,
            kind=direction,
            total=amt,
            account=acct,
            method=method,
            payment_date=pay_date,
            note=note,
            request_key=uuid4(),
            created_by=actor,
            **audit_fields(actor),
        )

        is_sale = direction == 'receipt'
        payment_ids = []
        for row in preview['allocations']:
            if acct.warehouse_id is not None and acct.warehouse_id != row['warehouse_id']:
                raise ValidationError({'account': 'Use a shared account or an account matching each document warehouse.'})
            alloc = Decimal(row['allocated'])
            res = _post_order_payment_impl(
                actor=actor,
                is_sale=is_sale,
                order_id=row['order_id'],
                tenders=[{'method': method, 'account_id': acct.pk, 'amount': str(alloc), 'note': note}],
                payment_date=pay_date,
                request_key=uuid4(),
                origin=PaymentOrigin.CLEAR_DUE,
                settlement=settlement,
                bypass_perm_check=True,
            )
            payment_ids.extend(res['payment_ids'])

        log_audit_event(
            actor, AuditLog.Action.PAYMENT, instance=settlement,
            changes={'party_id': party_id, 'direction': direction, 'total': preview['amount'], 'payment_ids': payment_ids},
            module=AuditLog.Module.ACCOUNTING, request_key=request_key,
        )
        out = {
            'settlement_id': settlement.pk,
            'payment_ids': payment_ids,
            'allocations': preview['allocations'],
            'total': preview['amount'],
        }
        return IdempotentResult('party_settlement', settlement.pk, out, out)

    result, _ = run_idempotent(request_key, 'clear_due', payload, execute)
    return result
