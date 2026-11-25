"""POS terminal checkout, register session lifecycle, and drafts (POS-01)."""

from decimal import Decimal
from uuid import uuid4

from django.db import OperationalError, transaction
from django.db.models import F, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from accounting.models import AccountTransaction, FinancialAccount, PaymentMethod, PaymentOrigin, SalesPayment
from accounting.services import lock_rows, post_sales_payment, post_transaction
from common.audit import log_audit_event
from common.exceptions import StateConflict
from common.idempotency import IdempotentResult, run_idempotent
from common.models import AuditLog, DiscountType, SettlementMode
from common.money import q_money, q_qty
from inventory.models import Product, UnitOfMeasure
from inventory.pricing import resolve_unit_price
from people.models import Biller, Party
from warehouse.models import WareHouse
from warehouse.scope import check_warehouse, lock_warehouse_scope, scoped_queryset, warehouse_scope
from .loyalty import redeem_coupon, redeem_points
from .models import CashRegisterSession, PosDraft, RegisterMovement, SalesOrder, SalesOrderItem
from .services import complete_sale


ZERO = Decimal('0')


def open_register(actor, warehouse_id: int, opening_cash, request_key) -> tuple[CashRegisterSession, bool]:
    if not (has_role_permissions(actor, ('sale.open_register',)) or has_role_permissions(actor, ('sale.use_pos',))):
        raise PermissionDenied('You do not have permission to open a cash register.')
    cash = q_money(opening_cash, digits=4)
    if cash < ZERO:
        raise ValidationError({'opening_cash': 'Opening cash cannot be negative.'})

    def _do():
        with transaction.atomic():
            scope = lock_warehouse_scope(actor)
            wh = WareHouse.objects.filter(pk=warehouse_id, is_active=True).first()
            if wh is None:
                raise ValidationError({'warehouse': 'Choose an active warehouse.'})
            check_warehouse(scope, wh.pk)
            if CashRegisterSession.objects.filter(user=actor, warehouse=wh, status=CashRegisterSession.Status.OPEN).exists():
                raise StateConflict('You already have an open cash register for this warehouse.')
            username = actor.get_username()
            session = CashRegisterSession.objects.create(
                user=actor, warehouse=wh, opening_cash=cash,
                status=CashRegisterSession.Status.OPEN, create_by=username, modified_by=username,
            )
            log_audit_event(
                actor, AuditLog.Action.CREATE, session,
                changes={'opening_cash': str(cash)}, request_key=request_key, warehouse=wh,
            )
            return IdempotentResult(
                'cash_register', session.pk, {'id': session.pk, 'opening_cash': str(cash)}, value=session,
            )

    return run_idempotent(
        request_key, f'open_register:{actor.pk}:{warehouse_id}',
        {'user': actor.pk, 'warehouse': warehouse_id, 'opening_cash': str(cash)},
        _do, loader=lambda r: CashRegisterSession.objects.get(pk=r.result_id),
    )


def register_summary(session_id: int) -> dict:
    session = CashRegisterSession.objects.select_related('warehouse', 'user').get(pk=session_id)
    pmts = SalesPayment.objects.filter(register_session=session, is_reversal=False)
    by_method = {}
    cash_sales = ZERO
    cash_refunds = ZERO
    for m, _label in PaymentMethod.choices:
        in_amt = pmts.filter(method=m, sales_return__isnull=True).aggregate(s=Sum('amount'))['s'] or ZERO
        out_amt = pmts.filter(method=m, sales_return__isnull=False).aggregate(s=Sum('amount'))['s'] or ZERO
        by_method[m] = {'sales': str(q_money(in_amt, digits=4)), 'refunds': str(q_money(out_amt, digits=4))}
        if m == PaymentMethod.CASH:
            cash_sales += in_amt
            cash_refunds += out_amt
    # Also include cash refunds returned against POS orders in this session even if refund posted separately
    order_ids = list(session.sales_orders.values_list('pk', flat=True))
    extra_refunds = SalesPayment.objects.filter(
        sales_order_id__in=order_ids, register_session__isnull=True,
        sales_return__isnull=False, is_reversal=False, method=PaymentMethod.CASH,
    ).aggregate(s=Sum('amount'))['s'] or ZERO
    cash_refunds += extra_refunds

    cash_in = RegisterMovement.objects.filter(session=session, kind=RegisterMovement.Kind.CASH_IN).aggregate(s=Sum('amount'))['s'] or ZERO
    cash_out = RegisterMovement.objects.filter(session=session, kind=RegisterMovement.Kind.CASH_OUT).aggregate(s=Sum('amount'))['s'] or ZERO
    expected_cash = q_money(Decimal(session.opening_cash) + cash_sales - cash_refunds + cash_in - cash_out, digits=4)
    return {
        'session_id': session.pk,
        'status': session.status,
        'warehouse': session.warehouse_id,
        'opening_cash': str(q_money(session.opening_cash, digits=4)),
        'cash_sales': str(q_money(cash_sales, digits=4)),
        'cash_refunds': str(q_money(cash_refunds, digits=4)),
        'cash_in': str(q_money(cash_in, digits=4)),
        'cash_out': str(q_money(cash_out, digits=4)),
        'expected_cash': str(expected_cash),
        'by_method': by_method,
    }


def record_register_movement(actor, session_id: int, kind: str, amount, *, account_id=None, note='', request_key=None):
    amt = q_money(amount, digits=4)
    if amt <= ZERO:
        raise ValidationError({'amount': 'Movement amount must be positive.'})
    if kind not in RegisterMovement.Kind.values:
        raise ValidationError({'kind': 'Choose cash_in or cash_out.'})
    key = request_key or uuid4()

    def _do():
        with transaction.atomic():
            scope = lock_warehouse_scope(actor)
            session = scoped_queryset(CashRegisterSession.objects.select_for_update(), scope).filter(pk=session_id).first()
            if session is None:
                raise NotFound('Cash register session not found.')
            if session.status != CashRegisterSession.Status.OPEN:
                raise StateConflict('Register session is closed.')
            acct = None
            if account_id:
                acct = lock_rows(FinancialAccount.objects.filter(pk=account_id, is_active=True)).first()
                if acct is None:
                    raise ValidationError({'account': 'Choose an active financial account.'})
                check_warehouse(scope, acct.warehouse_id) if acct.warehouse_id else None
            username = actor.get_username()
            mv = RegisterMovement.objects.create(
                session=session, kind=kind, amount=amt, note=note or '', account=acct,
                request_key=key, create_by=username, modified_by=username,
            )
            if acct is not None:
                direction = AccountTransaction.Direction.INFLOW if kind == RegisterMovement.Kind.CASH_IN else AccountTransaction.Direction.OUTFLOW
                post_transaction(
                    acct, direction, actor, date=timezone.localdate(), amount=amt,
                    source_kind=AccountTransaction.SourceKind.REGISTER_MOVEMENT,
                    source_ref_id=mv.pk, source_reference=f'Reg#{session.pk}',
                )
            return IdempotentResult('register_movement', mv.pk, {'id': mv.pk, 'amount': str(amt)}, value=mv)

    return run_idempotent(key, f'reg_mv:{session_id}', {'session_id': session_id, 'kind': kind, 'amount': str(amt)}, _do,
                          loader=lambda r: RegisterMovement.objects.get(pk=r.result_id))


def close_register(actor, session_id: int, counted_cash, notes: str = '', request_key=None) -> tuple[CashRegisterSession, bool]:
    if not (has_role_permissions(actor, ('sale.close_register',)) or has_role_permissions(actor, ('sale.use_pos',))):
        raise PermissionDenied('You do not have permission to close a cash register.')
    actual = q_money(counted_cash, digits=4)
    if actual < ZERO:
        raise ValidationError({'counted_cash': 'Counted cash cannot be negative.'})
    key = request_key or uuid4()

    def _do():
        with transaction.atomic():
            scope = lock_warehouse_scope(actor)
            CashRegisterSession.objects.filter(pk=session_id).update(updated_at=F('updated_at'))
            session = scoped_queryset(CashRegisterSession.objects.select_for_update(), scope).filter(pk=session_id).first()
            if session is None:
                raise NotFound('Cash register session not found.')
            if session.status == CashRegisterSession.Status.CLOSED:
                return IdempotentResult('cash_register_close', session.pk, {'id': session.pk}, value=session)
            summary = register_summary(session.pk)
            expected = Decimal(summary['expected_cash'])
            session.closing_cash_expected = expected
            session.closing_cash_actual = actual
            session.variance = q_money(actual - expected, digits=4)
            session.status = CashRegisterSession.Status.CLOSED
            session.closed_at = timezone.now()
            session.notes = notes or session.notes
            session.modified_by = actor.get_username()
            session.save(update_fields=[
                'closing_cash_expected', 'closing_cash_actual', 'variance',
                'status', 'closed_at', 'notes', 'modified_by', 'updated_at',
            ])
            log_audit_event(
                actor, AuditLog.Action.CLOSE, session,
                changes={'expected': str(expected), 'actual': str(actual), 'variance': str(session.variance)},
                request_key=key, warehouse=session.warehouse,
            )
            return IdempotentResult('cash_register_close', session.pk, {'id': session.pk, 'variance': str(session.variance)}, value=session)

    return run_idempotent(
        key, f'close_register:{session_id}', {'session_id': session_id, 'counted_cash': str(actual)}, _do,
        loader=lambda r: CashRegisterSession.objects.get(pk=r.result_id),
    )


def pos_checkout(actor, payload: dict, request_key) -> tuple[dict, bool]:
    if not (has_role_permissions(actor, ('sale.use_pos',)) or has_role_permissions(actor, ('sale.add_salesorder',))):
        raise PermissionDenied('You do not have permission to perform POS checkout.')

    def _do():
        with transaction.atomic():
            scope = lock_warehouse_scope(actor)
            reg_id = payload.get('register')
            wh_id = payload.get('warehouse')
            session = CashRegisterSession.objects.select_for_update().filter(
                pk=reg_id, user=actor, status=CashRegisterSession.Status.OPEN,
            ).first()
            if session is None:
                raise ValidationError({'register': 'An open cash register session for this user is required.'})
            if wh_id and int(wh_id) != session.warehouse_id:
                raise ValidationError({'warehouse': 'Register belongs to a different warehouse.'})
            wh = WareHouse.objects.filter(pk=session.warehouse_id, is_active=True).first()
            if wh is None:
                raise ValidationError({'warehouse': 'Warehouse is inactive.'})
            check_warehouse(scope, wh.pk)

            customer = None
            cust_id = payload.get('customer')
            if cust_id:
                customer = Party.objects.select_related('customer_group', 'discount_plan').filter(
                    pk=cust_id, is_customer=True, is_active=True,
                ).first()
                if customer is None:
                    raise ValidationError({'customer': 'Choose an active customer.'})

            biller = None
            biller_id = payload.get('biller')
            if biller_id:
                biller = Biller.objects.filter(pk=biller_id, is_active=True).first()
                if biller is None:
                    raise ValidationError({'biller': 'Choose an active biller.'})

            lines = payload.get('lines') or []
            if not lines:
                raise ValidationError({'lines': 'Add at least one item to POS cart.'})

            can_override = has_role_permissions(actor, ('sale.override_price',))
            today = timezone.localdate()
            username = actor.get_username()
            currency = payload.get('currency') or 'BDT'
            order_disc = payload.get('order_discount') or {}
            disc_type = order_disc.get('type') or DiscountType.FLAT
            disc_val = Decimal(str(order_disc.get('value') or '0'))
            shipping = Decimal(str(payload.get('shipping') or '0'))

            order = SalesOrder.objects.create(
                customer=customer,
                warehouse=wh,
                biller=biller,
                order_date=today,
                currency_id=currency,
                exchange_rate=Decimal('1'),
                settlement_mode=SettlementMode.EXPLICIT,
                register_session=session,
                channel='pos',
                order_discount_type=disc_type,
                order_discount_value=disc_val,
                shipping_cost=shipping,
                create_by=username,
                modified_by=username,
            )

            created_items = []
            for idx, entry in enumerate(lines, start=1):
                prod = Product.objects.select_for_update().filter(pk=entry.get('product')).first()
                if prod is None:
                    raise ValidationError({'lines': f'Line {idx}: product not found.'})
                unit_id = entry.get('unit') or prod.sale_unit_id or prod.unit_id
                unit = UnitOfMeasure.objects.filter(pk=unit_id).first()
                if unit is None:
                    raise ValidationError({'lines': f'Line {idx}: unit not found.'})
                qty = q_qty(entry.get('quantity') or 0)
                if qty <= ZERO:
                    raise ValidationError({'lines': f'Line {idx}: quantity must be positive.'})

                manual = entry.get('manual_price')
                if manual is not None and not can_override:
                    raise PermissionDenied('You do not have permission to override product price.')

                resolved = resolve_unit_price(
                    prod, warehouse=wh, customer=customer, date=today, quantity=qty,
                    manual_price=Decimal(str(manual)) if manual is not None else None,
                )
                unit_price = resolved.price
                # Scale price when sold in a non-base unit (e.g. box = 12 pcs) unless manual price given
                factor = unit.conversion_factor_to(prod.unit)
                if manual is None and factor != Decimal('1'):
                    unit_price = q_money(unit_price * factor, digits=4)

                item = SalesOrderItem.objects.create(
                    sales_order=order,
                    line_number=idx,
                    product=prod,
                    unit=unit,
                    quantity=qty,
                    unit_price=unit_price,
                    tax_rate=resolved.tax_rate,
                    tax_method=resolved.tax_method,
                    batch_id=entry.get('batch'),
                    serial_numbers=list(entry.get('serials') or []),
                    applied_discount_rule_id=resolved.rule_id if resolved.source == 'discount_plan' else None,
                    create_by=username,
                    modified_by=username,
                )
                created_items.append(item)

            coupon_code = (payload.get('coupon_code') or '').strip()
            if coupon_code:
                line_sub = order.subtotal
                c_disc = redeem_coupon(coupon_code, line_sub, sales_order=order, customer=customer, doc_date=today, actor=actor)
                order.coupon_code = coupon_code.upper()
                order.coupon_discount_amount = c_disc
                if order.order_discount_type == DiscountType.FLAT:
                    order.order_discount_value = Decimal(order.order_discount_value) + c_disc
                order.save(update_fields=['coupon_code', 'coupon_discount_amount', 'order_discount_value', 'updated_at'])

            redeemed_info = None
            redeem_pts = int(payload.get('redeem_points') or 0)
            if redeem_pts > 0:
                if customer is None:
                    raise ValidationError({'customer': 'Select a customer to redeem reward points.'})
                redeemed_info = redeem_points(
                    customer.pk, redeem_pts, sales_order=order, actor=actor,
                    request_key=f'redeem:pos:{request_key}',
                )
                r_amt = Decimal(redeemed_info['monetary_value'])
                if order.order_discount_type == DiscountType.FLAT:
                    order.order_discount_value = Decimal(order.order_discount_value) + r_amt
                    order.save(update_fields=['order_discount_value', 'updated_at'])

            tenders = payload.get('tenders') or []
            complete_sale(
                actor, order.pk,
                settlement_mode=SettlementMode.EXPLICIT,
                tenders=tenders,
                payment_date=today,
                request_key=uuid4(),
                register_session_id=session.pk,
                bypass_perm_check=True,
            )
            order.refresh_from_db()
            summary = register_summary(session.pk)
            res = {
                'sales_order_id': order.pk,
                'invoice_no': order.order_number,
                'payment_status': order.payment_status,
                'subtotal': str(order.subtotal),
                'tax_total': str(order.tax_total),
                'grand_total': str(order.grand_total),
                'drawer_impact': summary,
                'print_url': f'/report/api/pos/{order.pk}/receipt/',
            }
            if redeemed_info:
                res['redeemed_points'] = redeemed_info
            return IdempotentResult('pos_checkout', order.pk, res, value=res)

    return run_idempotent(request_key, f'pos_checkout:{actor.pk}', payload, _do)
