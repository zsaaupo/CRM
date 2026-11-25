from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from uuid import uuid4

from accounting.models import PaymentOrigin
from accounting.services import lock_payment_account, post_completed_sale_payment, post_sales_payment, refresh_payment_status
from common.audit import log_audit_event
from common.exceptions import IdempotencyConflict
from common.idempotency import IdempotentResult, run_idempotent
from common.models import AuditLog, SettlementMode, TaxMethod
from common.money import convert_to_base, q_cost, q_money, q_qty
from common.pricing import calculate_document_totals
from inventory.models import Product, require_convertible_unit
from people.models import Party
from purchase.models import PurchaseOrder, PurchaseOrderItem
from report.services import document_context
from warehouse.models import StockMovement, WareHouse
from warehouse.scope import check_warehouse, lock_warehouse_scope, scoped_queryset, warehouse_scope
from warehouse.services import post_stock_movement
from account.permissions import has_role_permissions
from .models import (
    QuotationConversion, SalesOrder, SalesOrderItem, SalesQuotation, SalesQuotationItem, quotation_valid_until,
)


class SaleCompleted(APIException):
    status_code = 409
    default_detail = 'Completed sales and their items are read-only and cannot be changed or deleted.'
    default_code = 'sale_completed'


class SaleBusy(APIException):
    status_code = 409
    default_detail = 'This sale or stock is being updated. Retry the request; completing the same sale does not deduct stock twice.'
    default_code = 'sale_busy'


def lock_order(pk, actor):
    """Call inside atomic. Serialize order/line edits with completion, including on SQLite."""
    try:
        scope = lock_warehouse_scope(actor)
        orders = scoped_queryset(SalesOrder.objects.all(), scope)
        orders.filter(pk=pk, is_completed=False).update(is_completed=False)
        order = orders.select_for_update().get(pk=pk)
    except SalesOrder.DoesNotExist as exc:
        raise NotFound('Sales order not found.') from exc
    if order.is_completed:
        raise SaleCompleted()
    return order


def require_item_permission(request, action):
    if request is not None and not has_role_permissions(request.user, (f'sale.{action}_salesorderitem',)):
        raise PermissionDenied(f'You do not have permission to {action} sales order items.')


@transaction.atomic
def delete_order(order, request):
    order = lock_order(order.pk, request.user)
    if not has_role_permissions(request.user, ('sale.delete_salesorder',)):
        raise PermissionDenied('Your role does not have permission to delete sales orders.')
    if order.items.exists():
        require_item_permission(request, 'delete')
        order.items.all().delete()
    order.delete()


def complete_sale(
    actor, sale_id, *, settlement_mode=None, tenders=None, request_key=None,
    payment_date=None, register_session_id=None, bypass_perm_check=False,
):
    if not bypass_perm_check and not has_role_permissions(actor, ('sale.change_salesorder',)):
        raise PermissionDenied('You do not have permission to complete a sale.')
    try:
        with transaction.atomic():
            scope = lock_warehouse_scope(actor)
            if not bypass_perm_check and not has_role_permissions(actor, ('sale.change_salesorder',)):
                raise PermissionDenied('Your role does not have permission to complete a sale.')
            orders = scoped_queryset(SalesOrder.objects.all(), scope)
            now = timezone.now()
            # Claim once as the first write; errors roll back both the flag and stock.
            claimed = orders.filter(pk=sale_id, is_completed=False).update(
                is_completed=True, fulfilled_at=now, modified_by=actor.get_username(), updated_at=now)
            try:
                order = orders.select_for_update().get(pk=sale_id)
            except SalesOrder.DoesNotExist as exc:
                raise NotFound('Sales order not found.') from exc
            if not claimed:
                return order, False
            if not order.warehouse_id:
                raise ValidationError({'warehouse': 'Set the warehouse on this sale before completing it.'})
            warehouse = WareHouse.objects.select_for_update().filter(pk=order.warehouse_id).first()
            if warehouse is None or not warehouse.is_active:
                raise ValidationError({'warehouse': 'Choose an active warehouse.'})
            items = list(order.items.order_by('line_number', 'pk'))
            if not items:
                raise ValidationError({'items': 'Add at least one sale item before completing.'})
            products = {product.pk: product for product in Product.objects.select_for_update().prefetch_related('combo_items').filter(
                pk__in={item.product_id for item in items}).order_by('pk')}
            factors = {}
            for item in items:
                product = products.get(item.product_id)
                if product is None:
                    raise ValidationError({'items': f'Line {item.line_number}: product missing.'})
                factors[item.pk] = require_convertible_unit(item.unit, product.unit, field='items')
                if item.quantity <= 0:
                    raise ValidationError({'items': 'Sale quantities must be positive.'})
            from warehouse.tracking import allocate_tracking
            from warehouse.models import StockSerial
            for item in sorted(items, key=lambda row: (row.product_id, row.line_number, row.pk)):
                product = products[item.product_id]
                factor = factors[item.pk]
                base_qty = q_qty(item.quantity * factor)
                if item.base_quantity != base_qty or item.unit_conversion_factor != factor:
                    item.unit_conversion_factor = factor
                    item.base_quantity = base_qty
                    item.save(update_fields=['unit_conversion_factor', 'base_quantity', 'updated_at'])
                if product.product_type_id in ('service', 'digital'):
                    continue
                if product.product_type_id == 'combo':
                    comps = list(product.combo_items.select_related('component_product').order_by('component_product_id'))
                    total_cogs = Decimal('0')
                    for comp in comps:
                        if comp.component_product.product_type_id in ('service', 'digital'):
                            continue
                        comp_qty = q_qty(base_qty * comp.quantity)
                        move = post_stock_movement(
                            warehouse_id=warehouse.pk,
                            product_id=comp.component_product_id,
                            kind=StockMovement.Kind.SALE_ISSUE,
                            quantity_delta=-comp_qty,
                            movement_date=order.order_date,
                            reference_type='sales_order',
                            reference_id=order.pk,
                            reference_line_id=item.pk,
                            posting_key=f'sales_order:{order.pk}:line:{item.pk}:comp:{comp.component_product_id}',
                            actor=actor,
                        )
                        total_cogs += Decimal(move.total_cost_base or 0)
                    item.unit_cost_base = q_cost(total_cogs / base_qty) if base_qty else Decimal('0')
                    item.save(update_fields=['unit_cost_base', 'updated_at'])
                    continue
                batch_obj, serial_objs = allocate_tracking(
                    product, warehouse.pk, base_qty,
                    batch_id=item.batch_id, serials=item.serial_numbers, doc_date=order.order_date,
                )
                move = post_stock_movement(
                    warehouse_id=warehouse.pk,
                    product_id=item.product_id,
                    kind=StockMovement.Kind.SALE_ISSUE,
                    quantity_delta=-base_qty,
                    batch_id=batch_obj.pk if batch_obj else None,
                    serial_ids=[s.pk for s in serial_objs],
                    serial_status=StockSerial.Status.SOLD,
                    movement_date=order.order_date,
                    reference_type='sales_order',
                    reference_id=order.pk,
                    reference_line_id=item.pk,
                    posting_key=f'sales_order:{order.pk}:line:{item.pk}:issue',
                    actor=actor,
                )
                item.unit_cost_base = move.unit_cost_base
                item.save(update_fields=['unit_cost_base', 'updated_at'])

            mode = settlement_mode or order.settlement_mode or SettlementMode.AUTO_FULL
            if mode not in SettlementMode.values:
                raise ValidationError({'settlement_mode': 'Choose auto_full, deferred, or explicit.'})

            # Freeze document-level pricing allocations and biller snapshot (G-07, D-28)
            totals = calculate_document_totals(
                [
                    {
                        'quantity': item.quantity,
                        'unit_price': item.unit_price,
                        'discount_amount': item.discount_amount,
                        'tax_rate': item.tax_rate,
                        'tax_method': item.tax_method,
                    }
                    for item in items
                ],
                order_discount_type=order.order_discount_type,
                order_discount_value=order.order_discount_value,
                order_tax_rate=order.order_tax_rate,
                shipping_cost=order.shipping_cost,
                digits=4,
            )
            for item, priced in zip(items, totals.lines):
                item.allocated_order_discount = priced.allocated_order_discount
                item.allocated_order_tax = priced.allocated_order_tax
                item.save(update_fields=['allocated_order_discount', 'allocated_order_tax', 'updated_at'])

            order.order_discount_amount = totals.order_discount_amount
            order.order_tax_amount = totals.order_tax_amount
            if order.biller_id and not order.biller_name_snapshot:
                order.biller_name_snapshot = order.biller.name
            recognized = convert_to_base(totals.grand_total, order.exchange_rate, digits=4)
            update_fields = [
                'order_discount_amount', 'order_tax_amount', 'biller_name_snapshot',
                'recognized_total_base', 'updated_at',
            ]
            order.recognized_total_base = recognized
            if order.settlement_mode != mode:
                order.settlement_mode = mode
                update_fields.append('settlement_mode')
            order.save(update_fields=update_fields)

            local_date = payment_date or timezone.localdate(now)
            if mode == SettlementMode.AUTO_FULL:
                post_completed_sale_payment(order, scope, actor, date=local_date)
            elif mode == SettlementMode.EXPLICIT and tenders:
                post_sales_payment(
                    actor, order.pk, tenders, local_date, request_key or uuid4(),
                    origin=PaymentOrigin.POS if register_session_id else PaymentOrigin.MANUAL,
                    register_session_id=register_session_id,
                    bypass_perm_check=True,
                )
            refresh_payment_status(order)
            from .loyalty import maybe_earn_points_for_order
            maybe_earn_points_for_order(order, actor=actor)
            try:
                from common.models import POSSetting, SMSTemplate
                from common.ops_services import enqueue_outbox_message
                pos_cfg = POSSetting.objects.first()
                if pos_cfg and pos_cfg.sms_after_sale and order.customer_id and order.customer.phone:
                    tpl = SMSTemplate.objects.filter(is_active=True).order_by('pk').first()
                    enqueue_outbox_message(
                        channel='sms',
                        recipient=order.customer.phone,
                        source_event_key=f'sale:{order.pk}:after_sale_sms',
                        template=tpl.code if tpl else '',
                        payload={
                            'order_id': order.pk,
                            'order_number': order.order_number,
                            'customer': order.customer.name,
                            'grand_total': str(order.grand_total),
                        },
                    )
            except Exception:
                # Outbox dispatch must not roll back committed sale if optional SMS template missing
                raise
            log_audit_event(
                actor, AuditLog.Action.COMPLETE, instance=order,
                changes={'settlement_mode': mode, 'payment_status': order.payment_status},
                module=AuditLog.Module.SALE, request_key=request_key, warehouse=warehouse,
            )
            return order, True
    except OperationalError as exc:
        if 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
            raise SaleBusy() from exc
        raise
    except IntegrityError as exc:
        raise SaleBusy('The sale could not be completed. Check the stock references and retry.') from exc
    except InvalidOperation as exc:
        raise ValidationError({'quantity': 'The stock quantity exceeds the database precision. The sale was not completed.'}) from exc


@transaction.atomic
def duplicate_sales_order(actor, order_id):
    if not has_role_permissions(actor, ('sale.view_salesorder', 'sale.add_salesorder')):
        raise PermissionDenied('You do not have permission to duplicate sales orders.')
    scope = lock_warehouse_scope(actor)
    source = scoped_queryset(SalesOrder.objects.prefetch_related('items'), scope).filter(pk=order_id).first()
    if source is None:
        raise NotFound('Sales order not found.')
    username = actor.get_username()
    clone = SalesOrder.objects.create(
        customer=source.customer,
        warehouse=source.warehouse,
        payment_account=source.payment_account,
        settlement_mode=source.settlement_mode,
        order_date=timezone.localdate(),
        expected_delivery_date=None,
        currency=source.currency,
        exchange_rate=source.exchange_rate,
        status_id='draft',
        customer_reference=source.customer_reference,
        billing_address=source.billing_address,
        shipping_address=source.shipping_address,
        shipping_cost=source.shipping_cost,
        order_discount_type=source.order_discount_type,
        order_discount_value=source.order_discount_value,
        order_tax_rule=source.order_tax_rule,
        order_tax_rate=source.order_tax_rate,
        biller=source.biller,
        staff_note=source.staff_note,
        sale_note=source.sale_note,
        notes=source.notes,
        create_by=username,
        modified_by=username,
    )
    for line in source.items.all():
        SalesOrderItem.objects.create(
            sales_order=clone,
            line_number=line.line_number,
            product=line.product,
            description=line.description,
            unit=line.unit,
            quantity=line.quantity,
            unit_price=line.unit_price,
            discount_amount=line.discount_amount,
            tax_rate=line.tax_rate,
            tax_method=line.tax_method,
            create_by=username,
            modified_by=username,
        )
    log_audit_event(actor, AuditLog.Action.CREATE, instance=clone, changes={'duplicated_from': source.pk}, module=AuditLog.Module.SALE)
    return clone

# Sales quotations

Status = SalesQuotation.Status
QUOTE_VIEW = ('sale.view_salesquotation',)
ITEM_VIEW = ('sale.view_salesquotationitem',)
QUOTE_DETAIL = (*QUOTE_VIEW, *ITEM_VIEW)
QUOTE_ADD = ('sale.add_salesquotation', 'sale.add_salesquotationitem')
QUOTE_EDIT = (*QUOTE_DETAIL, 'sale.change_salesquotation', 'sale.add_salesquotationitem',
              'sale.change_salesquotationitem', 'sale.delete_salesquotationitem')
QUOTE_DELETE = ('sale.delete_salesquotation', 'sale.delete_salesquotationitem')
ACTION_PERMISSIONS = {
    'issue': (*QUOTE_DETAIL, 'sale.issue_salesquotation'),
    'accept': (*QUOTE_DETAIL, 'sale.respond_salesquotation'),
    'decline': (*QUOTE_DETAIL, 'sale.respond_salesquotation'),
    'convert': (*QUOTE_DETAIL, 'sale.convert_salesquotation', 'sale.add_salesorder', 'sale.add_salesorderitem'),
    'revise': (*QUOTE_DETAIL, *QUOTE_ADD, 'sale.change_salesquotation'),
}
LINE_FIELDS = ('line_number', 'product_id', 'description', 'unit_id', 'quantity', 'unit_price', 'discount_amount', 'tax_rate')
SALES_FIELDS = ('customer_id', 'warehouse_id', 'currency_id', 'exchange_rate', 'customer_reference',
                'billing_address', 'shipping_address', 'shipping_cost')


class QuotationConflict(APIException):
    status_code = 409
    default_detail = 'This action is unavailable for the current quotation status.'
    default_code = 'quotation_conflict'


class QuotationBusy(APIException):
    status_code = 409
    default_detail = 'This quotation is being updated. Retry the request.'
    default_code = 'quotation_busy'


def require_permissions(user, permissions):
    if not has_role_permissions(user, permissions):
        raise PermissionDenied('Your role does not have permission for this quotation action.')


def lock_quotation(pk, scope):
    records = scoped_queryset(SalesQuotation.objects.filter(pk=pk), scope)
    # The no-op write serializes changes on SQLite, where select_for_update has no effect.
    records.update(status=F('status'))
    record = records.select_for_update().first()
    if record is None:
        raise NotFound('Quotation not found.')
    return record


def require_draft(quotation):
    if quotation.status != Status.DRAFT:
        raise QuotationConflict('Only draft quotations can be edited or deleted.')


def available_actions(quotation, user):
    actions = {
        Status.DRAFT: ('issue',),
        Status.ISSUED: ('accept', 'decline', 'revise') if not quotation.is_expired else ('revise',),
        Status.ACCEPTED: ('convert',),
        Status.DECLINED: ('revise',),
        Status.SUPERSEDED: ('revise',),
    }
    return [action for action in actions.get(quotation.status, ())
            if has_role_permissions(user, ACTION_PERMISSIONS[action])]


def eligible_lines(quotation):
    customer = Party.objects.select_for_update().filter(pk=quotation.customer_id, is_customer=True, is_active=True).first()
    if customer is None:
        raise ValidationError({'customer': 'Choose an active customer.'})
    warehouse = WareHouse.objects.select_for_update().filter(pk=quotation.warehouse_id, is_active=True).first()
    if warehouse is None:
        raise ValidationError({'warehouse': 'Choose an active warehouse.'})
    quotation.customer = customer
    quotation.warehouse = warehouse
    items = list(quotation.items.select_related('product', 'unit').select_for_update())
    if not items:
        raise ValidationError({'items': 'Add at least one product.'})
    from inventory.models import require_convertible_unit
    for item in items:
        require_convertible_unit(item.unit, item.product.unit, field='items')
    return items


def line_values(item):
    return {field: getattr(item, field) for field in LINE_FIELDS}


def sales_values(quotation, today):
    values = {field: getattr(quotation, field) for field in SALES_FIELDS}
    delivery = quotation.expected_delivery_date
    return {**values, 'order_date': today, 'expected_delivery_date': delivery if delivery and delivery >= today else None}


@transaction.atomic
def apply_quotation_action(actor, pk, action, *, payment_account=None, settlement_mode=None):
    permissions = ACTION_PERMISSIONS[action]
    require_permissions(actor, permissions)
    scope = lock_warehouse_scope(actor)
    require_permissions(actor, permissions)
    quotation = lock_quotation(pk, scope)
    username = actor.get_username()
    now = timezone.now()
    today = timezone.localdate(now)

    if action == 'issue':
        if quotation.status == Status.ISSUED:
            return quotation
        require_draft(quotation)
        if not quotation.order_date <= today <= quotation.valid_until:
            raise ValidationError({'valid_until': 'Issue the quotation within its validity period.'})
        items = eligible_lines(quotation)
        quotation.status = Status.ISSUED
        quotation.issued_at = now
        quotation.issued_by = username
        quotation.issued_document = document_context('quotation', quotation, items=items)
        fields = ['status', 'issued_at', 'issued_by', 'issued_document']
    elif action in ('accept', 'decline'):
        status = Status.ACCEPTED if action == 'accept' else Status.DECLINED
        if quotation.status == status:
            return quotation
        if quotation.status != Status.ISSUED or quotation.valid_until < today:
            raise QuotationConflict('Record responses to issued quotations within their validity period.')
        quotation.status = status
        quotation.responded_at = now
        quotation.responded_by = username
        fields = ['status', 'responded_at', 'responded_by']
    elif action == 'convert':
        if quotation.status == Status.CONVERTED:
            return quotation
        if quotation.status != Status.ACCEPTED:
            raise QuotationConflict('Accept the quotation before converting it to a sales order.')
        items = eligible_lines(quotation)
        mode = settlement_mode or SettlementMode.AUTO_FULL
        account = (
            lock_payment_account(scope, payment_account, quotation.warehouse_id)
            if (mode == SettlementMode.AUTO_FULL or payment_account is not None)
            else None
        )
        order = SalesOrder.objects.create(
            **sales_values(quotation, today), payment_account=account, settlement_mode=mode,
            notes=quotation.terms, create_by=username, modified_by=username,
        )
        SalesOrderItem.objects.bulk_create([
            SalesOrderItem(sales_order=order, create_by=username, modified_by=username, **line_values(item))
            for item in items
        ])
        QuotationConversion.objects.get_or_create(
            quotation_revision=quotation,
            kind=QuotationConversion.Kind.SALE,
            defaults={
                'target_type': 'sale.SalesOrder',
                'target_id': order.pk,
                'created_by': actor,
                'create_by': username,
                'modified_by': username,
            },
        )
        quotation.status = Status.CONVERTED
        quotation.sales_order = order
        fields = ['status', 'sales_order']
    else:
        if quotation.status not in (Status.ISSUED, Status.DECLINED, Status.SUPERSEDED):
            raise QuotationConflict('Only issued, declined, or superseded quotations can be revised.')
        revision = SalesQuotation.objects.filter(revised_from=quotation).first()
        if revision is not None:
            if not scoped_queryset(SalesQuotation.objects.filter(pk=revision.pk), scope).exists():
                raise NotFound('Quotation revision not found.')
            return revision
        if quotation.status == Status.ISSUED:
            quotation.status = Status.SUPERSEDED
            quotation.modified_by = username
            quotation.save(update_fields=['status', 'modified_by', 'updated_at'])
        revision = SalesQuotation.objects.create(
            **sales_values(quotation, today), revised_from=quotation, valid_until=quotation_valid_until(today),
            terms=quotation.terms, notes=quotation.notes, create_by=username, modified_by=username,
        )
        SalesQuotationItem.objects.bulk_create([
            SalesQuotationItem(quotation=revision, create_by=username, modified_by=username, **line_values(item))
            for item in quotation.items.all()
        ])
        return revision

    quotation.modified_by = username
    quotation.save(update_fields=[*fields, 'modified_by', 'updated_at'])
    return quotation


def convert_quotation_to_purchase(
    actor, quotation_id: int, supplier_id: int, cost_basis: str, request_key, *, preview: bool = False,
):
    if not (
        has_role_permissions(actor, ('sale.view_salesquotation',))
        and has_role_permissions(actor, ('purchase.add_purchaseorder',))
    ):
        raise PermissionDenied('Quotation-to-purchase conversion requires permissions in both sale and purchase.')
    if cost_basis not in ('product_cost', 'quoted_price'):
        raise ValidationError({'cost_basis': 'Choose product_cost or quoted_price.'})

    def _build_lines(quotation):
        items = list(quotation.items.select_related('product', 'product__unit', 'product__purchase_unit', 'unit').order_by('line_number', 'pk'))
        if not items:
            raise ValidationError({'items': 'Add at least one item before converting this quotation.'})
        out = []
        for idx, it in enumerate(items, start=1):
            prod = it.product
            q_factor = require_convertible_unit(it.unit, prod.unit, field='items')
            base_qty = q_qty(Decimal(it.quantity) * q_factor)
            p_unit = prod.purchase_unit or prod.unit
            p_factor = require_convertible_unit(p_unit, prod.unit, field='items')
            p_qty = q_qty(base_qty / p_factor)
            if cost_basis == 'product_cost':
                unit_cost = q_money(Decimal(prod.cost_price or 0) * p_factor / quotation.exchange_rate, digits=4)
            else:
                unit_cost = q_money(Decimal(it.unit_price) / q_factor * p_factor, digits=4)
            out.append({
                'line_number': idx,
                'product': prod,
                'unit': p_unit,
                'quantity': p_qty,
                'unit_price': unit_cost,
                'discount_amount': it.discount_amount,
                'tax_rate': it.tax_rate,
                'tax_method': it.tax_method,
            })
        return out

    if preview:
        scope = warehouse_scope(actor) if callable(globals().get('warehouse_scope')) else lock_warehouse_scope(actor)
        quotation = scoped_queryset(SalesQuotation.objects.all(), scope).filter(pk=quotation_id).first()
        if quotation is None:
            raise NotFound('Sales quotation not found.')
        supplier = Party.objects.filter(pk=supplier_id, is_supplier=True, is_active=True).first()
        if supplier is None:
            raise ValidationError({'supplier': 'Choose an active supplier.'})
        lines = _build_lines(quotation)
        return {
            'quotation_id': quotation.pk,
            'quotation_number': quotation.quotation_number,
            'supplier_id': supplier.pk,
            'cost_basis': cost_basis,
            'lines': [
                {
                    'line_number': ln['line_number'],
                    'product_id': ln['product'].pk,
                    'product_sku': ln['product'].sku,
                    'unit_id': ln['unit'].pk,
                    'unit_code': ln['unit'].code,
                    'quantity': str(ln['quantity']),
                    'unit_price': str(ln['unit_price']),
                }
                for ln in lines
            ],
        }

    def _do():
        with transaction.atomic():
            scope = lock_warehouse_scope(actor)
            quotation = scoped_queryset(SalesQuotation.objects.select_for_update(), scope).filter(pk=quotation_id).first()
            if quotation is None:
                raise NotFound('Sales quotation not found.')
            check_warehouse(scope, quotation.warehouse_id)

            existing_conv = QuotationConversion.objects.filter(
                quotation_revision=quotation, kind=QuotationConversion.Kind.PURCHASE,
            ).first()
            if existing_conv is not None:
                return IdempotentResult(
                    'quotation_purchase_conversion', existing_conv.target_id,
                    {'purchase_order_id': existing_conv.target_id},
                    value={'purchase_order_id': existing_conv.target_id},
                )

            supplier = Party.objects.filter(pk=supplier_id, is_supplier=True, is_active=True).first()
            if supplier is None:
                raise ValidationError({'supplier': 'Choose an active supplier.'})

            lines = _build_lines(quotation)
            username = actor.get_username()
            today = timezone.localdate()
            po = PurchaseOrder.objects.create(
                supplier=supplier,
                warehouse=quotation.warehouse,
                order_date=today,
                currency=quotation.currency,
                exchange_rate=quotation.exchange_rate,
                settlement_mode=SettlementMode.DEFERRED,
                notes=f'Converted from quotation {quotation.quotation_number}',
                create_by=username,
                modified_by=username,
            )
            for ln in lines:
                PurchaseOrderItem.objects.create(
                    purchase_order=po,
                    line_number=ln['line_number'],
                    product=ln['product'],
                    unit=ln['unit'],
                    quantity=ln['quantity'],
                    unit_price=ln['unit_price'],
                    discount_amount=ln['discount_amount'],
                    tax_rate=ln['tax_rate'],
                    tax_method=ln['tax_method'],
                    create_by=username,
                    modified_by=username,
                )
            QuotationConversion.objects.create(
                quotation_revision=quotation,
                kind=QuotationConversion.Kind.PURCHASE,
                target_type='purchase.PurchaseOrder',
                target_id=po.pk,
                request_key=request_key or uuid4(),
                created_by=actor,
                create_by=username,
                modified_by=username,
            )
            log_audit_event(
                actor, AuditLog.Action.CREATE, po,
                changes={'from_quotation': quotation.quotation_number, 'cost_basis': cost_basis},
                request_key=request_key, warehouse=quotation.warehouse,
            )
            return IdempotentResult(
                'quotation_purchase_conversion', po.pk,
                {'purchase_order_id': po.pk},
                value={'purchase_order_id': po.pk},
            )

    return run_idempotent(
        request_key, f'quote_to_purchase:{quotation_id}',
        {'quotation_id': quotation_id, 'supplier_id': supplier_id, 'cost_basis': cost_basis},
        _do,
    )
