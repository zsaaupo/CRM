from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Case, CharField, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import Coupon, GiftCard


CARD_COLUMNS = {'card_no': 'Card number', 'owner': 'Owner', 'amount': 'Loaded', 'expense': 'Spent',
                'balance': 'Balance', 'created_by': 'Issued by', 'expired_date': 'Expires', 'status': 'Status'}
COUPON_COLUMNS = {'code': 'Code', 'kind': 'Discount type', 'value': 'Value', 'quantity': 'Total uses',
                  'used': 'Used', 'remaining': 'Available', 'status': 'Status'}


def card_data(card):
    owner = card.customer or card.user
    return dict(id=card.pk, card_no=card.card_no, owner=str(owner) if owner else 'Bearer',
                amount=format(card.amount, '.2f'), expense=format(card.expense, '.2f'), balance=format(card.balance, '.2f'),
                expired_date=card.expired_date.isoformat(), created_by=card.created_by.get_username(),
                status='Inactive' if not card.is_active else 'Expired' if card.expired_date < timezone.localdate() else 'Active')


def coupon_data(coupon):
    return dict(id=coupon.pk, code=coupon.code, kind=coupon.kind, value=format(coupon.value, '.2f'),
                quantity=coupon.quantity, used=coupon.used, remaining=coupon.quantity - coupon.used,
                status='Inactive' if not coupon.is_active else 'Exhausted' if coupon.used == coupon.quantity else 'Active')


def list_instruments(is_card, params):
    search = params.get('search', '').strip()
    columns = CARD_COLUMNS if is_card else COUPON_COLUMNS
    fields = {key: key for key in columns}
    if is_card:
        records = GiftCard.objects.select_related('created_by', 'user', 'customer').annotate(net=F('amount') - F('expense'))
        if search:
            records = records.filter(Q(card_no__icontains=search) | Q(customer__name__icontains=search)
                                     | Q(user__username__icontains=search) | Q(created_by__username__icontains=search))
        records = records.annotate(owner_name=Coalesce('customer__name', 'user__username', Value('Bearer')),
                                   state=Case(When(is_active=False, then=Value('Inactive')),
                                              When(expired_date__lt=timezone.localdate(), then=Value('Expired')),
                                              default=Value('Active'), output_field=CharField()))
        fields.update(balance='net', owner='owner_name', created_by='created_by__username', status='state')
        totals = {key: format(value or 0, '.2f') for key, value in
                  records.aggregate(amount=Sum('amount'), expense=Sum('expense'), balance=Sum('net')).items()}
    else:
        records = Coupon.objects.filter(code__icontains=search).annotate(available=F('quantity') - F('used'))
        fields.update(remaining='available', status='is_active')
        totals = {}
    sort = params.get('sort_by', 'id')
    direction = params.get('sort_dir', 'desc')
    size = params.get('per_page', '10')
    if sort not in {*fields, 'id', 'created_at'} or direction not in ('asc', 'desc') or size not in ('10', '25', '50', 'all'):
        raise ValidationError('Invalid sort order or page size.')
    order = ('-' if direction == 'desc' else '') + fields.get(sort, sort)
    pager = Paginator(records.order_by(order, 'id'), max(1, records.count()) if size == 'all' else int(size))
    page = pager.get_page(params.get('page', 1))
    data = [card_data(row) if is_card else coupon_data(row) for row in page]
    return dict(data=data, totals=totals, currency=settings.INVENTORY_BASE_CURRENCY,
                meta=dict(current_page=page.number, pages=pager.num_pages, total_entries=pager.count))

