"""POS pricing, parking and settlement. Only final checkout moves funds and stock."""
import hashlib
import json
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from uuid import uuid4

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from accounting.models import AccountTransaction, MAX_AMOUNT, SalesPayment
from accounting.services import audit_fields, lock_payment_account, post_transaction
from inventory.models import Product
from people.models import Party
from warehouse.models import WareHouse
from warehouse.scope import lock_warehouse_scope, scoped_queryset
from .instruments import CENT, Kind, consume_coupon, coupon_discount, move_card
from .models import Coupon, GiftCard, POSTicket, SalesOrder, SalesOrderItem
from .services import SaleBusy, complete_sale

POS_PERMS = ('sale.view_salesorder', 'sale.view_salesorderitem', 'sale.add_salesorder',
             'sale.add_salesorderitem', 'sale.change_salesorder')
ZERO = Decimal('0.00')


def money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def portable(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, cls=DjangoJSONEncoder).encode()).hexdigest()


class LineInput(serializers.Serializer):
    product = serializers.IntegerField(min_value=1)
    quantity = serializers.DecimalField(14, 6, min_value=Decimal('.000001'))
    unit_discount = serializers.DecimalField(12, 4, min_value=ZERO, default=ZERO)


class CartInput(serializers.Serializer):
    warehouse = serializers.IntegerField(min_value=1)
    customer = serializers.IntegerField(min_value=1, allow_null=True, default=None)
    date = serializers.DateField(default=timezone.localdate)
    items = LineInput(many=True, allow_empty=False, max_length=200)
    discount_type = serializers.ChoiceField(('flat', 'percentage'), default='flat')
    discount = serializers.DecimalField(12, 2, min_value=ZERO, default=ZERO)
    coupon = serializers.CharField(max_length=40, allow_blank=True, default='')
    shipping = serializers.DecimalField(12, 2, min_value=ZERO, default=ZERO)
    tax_rate = serializers.DecimalField(7, 4, min_value=ZERO, max_value=Decimal(100), allow_null=True, default=None)

    def validate(self, data):
        if data['date'] > timezone.localdate():
            raise ValidationError('A sale date cannot be in the future.')
        if data['discount_type'] == 'percentage' and data['discount'] > 100:
            raise ValidationError('Percentage discount cannot exceed 100%.')
        if len({line['product'] for line in data['items']}) != len(data['items']):
            raise ValidationError('Combine duplicate products into one line.')
        data['coupon'] = data['coupon'].upper()
        return data


class TenderInput(serializers.Serializer):
    method = serializers.ChoiceField(('cash', 'card', 'cheque', 'gift_card', 'deposit'))
    amount = serializers.DecimalField(13, 2, min_value=CENT)
    account = serializers.IntegerField(min_value=1, allow_null=True, default=None)
    received = serializers.DecimalField(13, 2, min_value=ZERO, allow_null=True, default=None)
    card_no = serializers.RegexField(r'^[0-9]{16}$', required=False)
    reference = serializers.CharField(max_length=100, default='', allow_blank=True)


class TicketInput(serializers.Serializer):
    key = serializers.UUIDField()
    revision = serializers.IntegerField(min_value=0, default=0)
    cart = CartInput()


class CheckoutInput(TicketInput):
    quote = serializers.CharField(max_length=64)
    tenders = TenderInput(many=True, max_length=12, allow_empty=True)


def validate_input(serializer, data):
    instance = serializer(data=data)
    instance.is_valid(raise_exception=True)
    return instance.validated_data


def products():
    return Product.objects.filter(currency_id=settings.INVENTORY_BASE_CURRENCY, currency__is_active=True,
                                  status_id='active', status__is_active=True)


def price(product, date):
    return product.promo_price if product.has_active_promo(date) else product.sale_price


def warehouse_for(scope, pk):
    warehouse = scoped_queryset(WareHouse.objects.filter(is_active=True), scope, 'pk').filter(pk=pk).first()
    if warehouse is None:
        raise NotFound('Choose an accessible active warehouse.')
    return warehouse


def price_cart(cart, scope, *, lock=False):
    warehouse = warehouse_for(scope, cart['warehouse'])
    customer = None
    if cart['customer']:
        customers = Party.objects.filter(pk=cart['customer'], is_customer=True, is_active=True)
        customer = (customers.select_for_update() if lock else customers).first()
        if customer is None:
            raise ValidationError('Choose an active customer.')
    catalog = products().filter(pk__in=[row['product'] for row in cart['items']]).order_by('pk')
    indexed = {p.pk: p for p in (catalog.select_for_update() if lock else catalog)}
    lines, bases = [], []
    for number, row in enumerate(cart['items'], 1):
        product = indexed.get(row['product'])
        if product is None:
            raise ValidationError('A product is no longer available in the POS catalog.')
        unit_price = price(product, cart['date'])
        if row['unit_discount'] > unit_price:
            raise ValidationError(f'Discount exceeds the price of {product.sku}.')
        line = SalesOrderItem(line_number=number, product=product, unit_id=product.unit_id, description=product.name,
                              quantity=row['quantity'], unit_price=unit_price, pos_line=True,
                              discount_amount=(row['unit_discount'] * row['quantity']).quantize(Decimal('.0001'), rounding=ROUND_DOWN),
                              tax_rate=product.product_tax if cart['tax_rate'] is None else cart['tax_rate'])
        lines.append(line)
        bases.append(line.net_amount)
    subtotal = sum(bases, ZERO)
    discount = cart['discount']
    if cart['discount_type'] == 'percentage':
        discount = money(subtotal * discount / 100)
    if discount > subtotal:
        raise ValidationError('Order discount exceeds the subtotal.')
    eligible = subtotal - discount
    coupon_value = ZERO
    if cart['coupon']:
        coupons = Coupon.objects.filter(code=cart['coupon'])
        coupon = (coupons.select_for_update() if lock else coupons).first()
        if coupon is None:
            raise ValidationError('Unknown coupon code.')
        coupon_value = coupon_discount(coupon, eligible)
        if not coupon_value:
            raise ValidationError('Coupon discount rounds to zero.')
    # Allocate rounded cumulative shares; the last line absorbs the rounding remainder.
    running = allocated = tax = ZERO
    details = []
    for line, base in zip(lines, bases):
        running += base
        target = money((discount + coupon_value) * running / subtotal) if subtotal else ZERO
        gross = line.quantity * line.unit_price
        line.discount_amount = min(line.discount_amount + target - allocated,
                                   gross.quantize(Decimal('.0001'), rounding=ROUND_DOWN))
        allocated = target
        net, line_tax, total = line.calculate_amounts()
        floor = line.product.minimum_sale_price
        if floor and net < money(floor * line.quantity):
            raise ValidationError(f'{line.product.sku} would be below its minimum sale price.')
        if total > MAX_AMOUNT or line.discount_amount >= Decimal('1e14'):
            raise ValidationError('Line exceeds the supported amount.')
        tax += line_tax
        details.append(dict(product=line.product_id, name=line.description, sku=line.product.sku,
                            quantity=line.quantity, unit_price=line.unit_price, subtotal=base, net=net,
                            tax=line_tax, tax_rate=line.tax_rate, total=total))
    total = sum((row['total'] for row in details), cart['shipping'])
    if total > MAX_AMOUNT:
        raise ValidationError('Sale exceeds the supported amount.')
    summary = portable(dict(items=details, subtotal=subtotal, discount=discount, coupon=coupon_value, tax=tax,
                            shipping=cart['shipping'], total=total, currency=settings.INVENTORY_BASE_CURRENCY))
    summary['quote'] = digest([cart, summary])
    return warehouse, customer, lines, summary


def settle_tenders(order, scope, actor, tenders, total):
    if sum((p['amount'] for p in tenders), ZERO) != total:
        raise ValidationError('Tender amounts must equal the grand total.')
    receipt = []
    for p in tenders:
        method, amount = p['method'], p['amount']
        row = dict(method=method, amount=amount, reference=p['reference'])
        if p['received'] is not None and method != 'cash':
            raise ValidationError('Only cash accepts an amount received.')
        if method in ('gift_card', 'deposit'):
            if method == 'deposit':
                card = GiftCard.objects.filter(is_deposit=True, customer_id=order.customer_id).first()
            else:
                card = GiftCard.objects.filter(is_deposit=False, card_no=p.get('card_no', '')).first()
            if card is None:
                raise ValidationError('No matching stored balance was found.')
            move_card(card.pk, Kind.REDEEM, amount, actor, uuid4(), sale=order, owner_user_id=actor.pk)
            row['reference'] = 'Ending ' + card.card_no[-4:]
        else:
            account = lock_payment_account(scope, p['account'], order.warehouse_id)
            if account.type != ('cash' if method == 'cash' else 'bank'):
                raise ValidationError(f'Choose a compatible account for {method}.')
            if method != 'cash' and not p['reference']:
                raise ValidationError('Enter the terminal or cheque reference.')
            if method == 'cash':
                received = amount if p['received'] is None else p['received']
                if received < amount:
                    raise ValidationError('Cash received is less than the cash tender.')
                row.update(received=received, change=received - amount)
            payment = SalesPayment.objects.create(sales_order=order, amount=amount, account=account, date=order.order_date,
                                                  method=account.type, reference=p['reference'] or order.order_number,
                                                  **audit_fields(actor))
            post_transaction(account, AccountTransaction.Direction.INFLOW, actor, date=order.order_date,
                             amount=amount, sales_payment=payment)
            row['account'] = account.name
        receipt.append(portable(row))
    return receipt


@transaction.atomic
def save_ticket(actor, data, *, checkout=False):
    scope = lock_warehouse_scope(actor)
    if not has_role_permissions(actor, POS_PERMS):
        raise PermissionDenied()
    cart = data['cart']
    warehouse = warehouse_for(scope, cart['warehouse'])
    ticket, _ = POSTicket.objects.select_for_update().get_or_create(
        key=data['key'], defaults={'cashier': actor, 'warehouse': warehouse})
    if ticket.cashier_id != actor.pk or ticket.warehouse_id != warehouse.pk:
        raise NotFound('Ticket not found.')
    signature = digest(data)
    if not ticket.cancelled and ticket.request_digest == signature:
        return ticket
    if ticket.order_id or ticket.cancelled or ticket.revision != data['revision']:
        raise SaleBusy('Ticket has changed. Reopen it from recent transactions.')
    warehouse, customer, lines, summary = price_cart(cart, scope, lock=checkout)
    ticket.cart = portable(cart)
    if checkout:
        if data['quote'] != summary['quote']:
            raise SaleBusy('Prices have changed. Refresh the quote and confirm the new total.')
        audit = audit_fields(actor)
        order = SalesOrder.objects.create(customer=customer, warehouse=warehouse, order_date=cart['date'],
                                          currency_id=settings.INVENTORY_BASE_CURRENCY, exchange_rate=1,
                                          shipping_cost=cart['shipping'], **audit)
        for line in lines:
            line.sales_order = order
            line.create_by = line.modified_by = actor.get_username()
            line.save()
        if cart['coupon']:
            consume_coupon(cart['coupon'], order, Decimal(summary['subtotal']) - Decimal(summary['discount']))
        def settle(sale, locked_scope, user):
            ticket.receipt = dict(summary, tenders=settle_tenders(sale, locked_scope, user, data['tenders'], sale.grand_total),
                                  number=sale.order_number, warehouse=warehouse.name, customer=customer.name if customer else 'Walk-in',
                                  cashier=actor.get_username(), date=cart['date'].isoformat(), coupon_code=cart['coupon'])
        complete_sale(actor, order.pk, settle=settle)
        ticket.order = order
    ticket.request_digest = signature
    ticket.revision += 1
    ticket.save()
    return ticket
