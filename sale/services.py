from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from accounting.services import lock_payment_account, post_completed_sale_payment
from audit_log.service import bulk_create_with_audit, record_activity
from inventory.models import Product
from people.models import Party
from report.services import document_context
from warehouse.models import WareHouse
from warehouse.scope import lock_warehouse_scope, scoped_queryset
from warehouse.services import deduct_stock
from account.permissions import has_role_permissions
from .models import Chalan, PackingSlip, SalesOrder, SalesOrderItem, SalesQuotation, SalesQuotationItem, quotation_valid_until


class SaleCompleted(APIException):
    status_code = 409
    default_detail = 'Completed sales and their items are read-only and cannot be changed or deleted.'
    default_code = 'sale_completed'


class ChalanDelivered(APIException):
    status_code = 409
    default_detail = 'Delivered Chalans are read-only.'
    default_code = 'chalan_delivered'


class ChalanStockInTransit(APIException):
    status_code = 409
    default_detail = 'Record the return before deleting a Chalan with stock still with the courier.'
    default_code = 'chalan_stock_in_transit'


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


def lock_chalan(scope, **lookup):
    """Lock the Chalan before changing its items or linked workflow records."""
    records = scoped_queryset(Chalan.objects.all(), scope).filter(**lookup)
    records.update(updated_at=F('updated_at'))
    return get_object_or_404(records.select_for_update())


def require_editable_chalan(chalan):
    if chalan.is_delivered:
        raise ChalanDelivered()


@transaction.atomic
def delete_chalan(pk, actor):
    scope = lock_warehouse_scope(actor)
    if not has_role_permissions(actor, ('sale.delete_chalan',)):
        raise PermissionDenied('Your role does not have permission to delete Chalans.')
    chalan = lock_chalan(scope, pk=pk)
    require_editable_chalan(chalan)
    if chalan.stock_in_transit:
        raise ChalanStockInTransit()
    if chalan.items.exists() and not has_role_permissions(actor, ('sale.delete_chalanitem',)):
        raise PermissionDenied('Your role does not have permission to delete Chalan items.')

    delivery = getattr(chalan, 'delivery', None)
    if delivery:
        delivery.status_updates.all().delete()
        delivery.delete()
    PackingSlip.objects.filter(chalan=chalan).delete()
    chalan.items.all().delete()
    chalan.delete()


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


def complete_sale(actor, sale_id, *, settle=None):
    if not has_role_permissions(actor, ('sale.change_salesorder',)):
        raise PermissionDenied('You do not have permission to complete a sale.')
    try:
        with transaction.atomic():
            scope = lock_warehouse_scope(actor)
            if not has_role_permissions(actor, ('sale.change_salesorder',)):
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
            products = {product.pk: product for product in Product.objects.select_for_update().filter(
                pk__in={item.product_id for item in items}).order_by('pk')}
            quantities = defaultdict(Decimal)
            for item in items:
                product = products.get(item.product_id)
                if product is None or item.unit_id != product.unit_id:
                    raise ValidationError({'items': f'Line {item.line_number}: the sale unit must match the product unit.'})
                if item.quantity <= 0:
                    raise ValidationError({'items': 'Sale quantities must be positive.'})
                quantities[item.product_id] += item.quantity
            deduct_stock(warehouse, quantities, products, actor, now)
            if settle is None:
                post_completed_sale_payment(order, scope, actor, date=timezone.localdate(now))
            else:
                settle(order, scope, actor)
            record_activity(order, 'completed', actor=actor)
            return order, True
    except OperationalError as exc:
        if 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
            raise SaleBusy() from exc
        raise
    except IntegrityError as exc:
        raise SaleBusy('The sale could not be completed. Check the stock references and retry.') from exc
    except InvalidOperation as exc:
        raise ValidationError({'quantity': 'The stock quantity exceeds the database precision. The sale was not completed.'}) from exc

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
    for item in items:
        if item.unit_id != item.product.unit_id:
            raise ValidationError({'items': f'Line {item.line_number}: the quoted unit must match the current product unit.'})
    return items


def line_values(item):
    return {field: getattr(item, field) for field in LINE_FIELDS}


def sales_values(quotation, today):
    values = {field: getattr(quotation, field) for field in SALES_FIELDS}
    delivery = quotation.expected_delivery_date
    return {**values, 'order_date': today, 'expected_delivery_date': delivery if delivery and delivery >= today else None}


@transaction.atomic
def apply_quotation_action(actor, pk, action, *, payment_account=None):
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
        account = lock_payment_account(scope, payment_account, quotation.warehouse_id)
        order = SalesOrder.objects.create(
            **sales_values(quotation, today), payment_account=account, notes=quotation.terms,
            create_by=username, modified_by=username,
        )
        bulk_create_with_audit(SalesOrderItem.objects, [
            SalesOrderItem(sales_order=order, create_by=username, modified_by=username, **line_values(item))
            for item in items
        ])
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
        bulk_create_with_audit(SalesQuotationItem.objects, [
            SalesQuotationItem(quotation=revision, create_by=username, modified_by=username, **line_values(item))
            for item in quotation.items.all()
        ])
        return revision

    quotation.modified_by = username
    quotation.save(update_fields=[*fields, 'modified_by', 'updated_at'])
    return quotation
