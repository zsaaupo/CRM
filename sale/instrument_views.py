from io import BytesIO
from uuid import uuid4

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe
from PIL import Image, ImageDraw, ImageFont
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from account.page_views import ProtectedAccountPageView
from account.permissions import has_role_permissions
from common.csv_files import csv_response
from report.rendering import render_pdf
from warehouse.scope import has_warehouse_scope
from warehouse.stock_count_files import csv_text, xlsx_response
from .instrument_forms import AmountForm, CouponForm, ExpiryForm, GiftCardForm
from .instrument_lists import CARD_COLUMNS, COUPON_COLUMNS, card_data, coupon_data, list_instruments
from .instruments import Kind, coupon_discount, insert_coded, issue_card, move_card, validate_card
from .models import Coupon, GiftCard


def allowed(user, action, is_card):
    model = 'giftcard' if is_card else 'coupon'
    return has_role_permissions(user, (f'sale.{action}_{model}',))


def require(user, action, is_card):
    if not allowed(user, action, is_card):
        raise PermissionDenied


def checked(form):
    if not form.is_valid():
        raise ValidationError(dict(form.errors))
    return form.cleaned_data


class InstrumentPage(ProtectedAccountPageView):
    template_name = 'sale/instruments.html'
    is_card = True

    def has_permission(self):
        return allowed(self.request.user, 'view', self.is_card)

    def get_context_data(self, **kwargs):
        user = self.request.user
        title = 'Gift cards' if self.is_card else 'Coupons'
        form = GiftCardForm if self.is_card else CouponForm
        return super().get_context_data(
            **kwargs, is_card=self.is_card, title=title, page_label=title, page_section='sale',
            sale_area='gift-cards' if self.is_card else 'coupons', currency=settings.INVENTORY_BASE_CURRENCY,
            columns=(CARD_COLUMNS if self.is_card else COUPON_COLUMNS).items(),
            form=form() if allowed(user, 'add', self.is_card) else None,
            can_change=allowed(user, 'change', self.is_card))


def export_table(report, params, is_card):
    columns = CARD_COLUMNS if is_card else COUPON_COLUMNS
    selected = set(params.getlist('selected'))
    rows = [[str(row[key]) for key in columns] for row in report['data'] if not selected or str(row['id']) in selected]
    headers = list(columns.values())
    name = 'gift-cards' if is_card else 'coupons'
    kind = params['export']
    if kind == 'csv':
        return csv_response(name + '.csv', headers, [[csv_text(cell) for cell in row] for row in rows])
    if kind == 'xlsx':
        return xlsx_response(name + '.xlsx', headers, rows, sheet_name='Gift cards' if is_card else 'Coupons')
    if kind == 'pdf':
        pdf = render_pdf(dict(company={}, headers=headers, rows=rows, title=name.replace('-', ' ').title()),
                         'sale/instrument_export.html')
        return HttpResponse(pdf, content_type='application/pdf',
                            headers={'Content-Disposition': f'attachment; filename="{name}.pdf"'})
    raise ValidationError('Invalid export format.')


def handle_conflict(exc):
    if isinstance(exc, IntegrityError) or 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
        return Response({'detail': 'Concurrent update. Refresh and retry with the same request key.'}, status=409)
    raise exc


@never_cache
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def collection(request, is_card):
    if request.method == 'GET':
        require(request.user, 'view', is_card)
        report = list_instruments(is_card, request.query_params)
        if request.query_params.get('export'):
            return export_table(report, request.query_params, is_card)
        return Response(report)
    require(request.user, 'add', is_card)
    payload = request.data.copy()
    if is_card:
        for key in ('customer', 'user'):
            if key + '_id' in payload:
                payload[key] = request.data[key + '_id']
    data = checked(GiftCardForm(payload) if is_card else CouponForm(payload))
    try:
        if is_card:
            return Response(card_data(issue_card(data, request.user)), status=201)
        coupon = insert_coded(Coupon, 'coupon', data.pop('code'), created_by=request.user, **data)
        return Response(coupon_data(coupon), status=201)
    except (IntegrityError, OperationalError) as exc:
        return handle_conflict(exc)


@never_cache
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def validate_instrument(request, is_card):
    if not allowed(request.user, 'view', is_card):
        if not has_warehouse_scope(request.user) or not has_role_permissions(request.user, ('sale.add_salesorder',)):
            raise PermissionDenied
    if is_card:
        card = get_object_or_404(GiftCard, card_no=request.data.get('card_no'))
        try:
            customer = int(request.data.get('customer_id') or 0)
        except (TypeError, ValueError):
            raise ValidationError('Invalid customer ID.')
        validate_card(card, customer, request.user.pk)
        result = dict(valid=True, balance=format(card.balance, '.2f'), expired_date=card.expired_date)
        if 'payable_amount' in request.data:
            due = checked(AmountForm({'amount': request.data['payable_amount']}))['amount']
            tender = min(due, card.balance)
            result.update(tender_amount=format(tender, '.2f'), remaining_due=format(due - tender, '.2f'))
        return Response(result)
    coupon = get_object_or_404(Coupon, code=str(request.data.get('code', '')).upper())
    value = checked(AmountForm({'amount': request.data.get('subtotal')}))['amount']
    return Response(dict(valid=True, discount=format(coupon_discount(coupon, value), '.2f')))


@never_cache
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def manage_instrument(request, is_card, action, pk=None):
    require(request.user, 'change', is_card)
    model = GiftCard if is_card else Coupon
    try:
        with transaction.atomic():
            if action == 'deactivate':
                ids = request.data.getlist('ids') if hasattr(request.data, 'getlist') else request.data.get('ids')
                if not isinstance(ids, list) or not ids:
                    raise ValidationError('Select at least one record.')
                try:
                    ids = {int(value) for value in ids}
                except (TypeError, ValueError):
                    raise ValidationError('Invalid selection.')
                records = model.objects.filter(pk__in=ids)
                records.update(is_active=F('is_active'))
                if is_card and records.filter(entries__kind=Kind.REDEEM).exists():
                    raise ValidationError('Only unused gift cards can be deactivated.')
                if records.update(is_active=False) != len(ids):
                    raise ValidationError('Unknown selection.')
                return Response({'updated': len(ids)})
            card = get_object_or_404(GiftCard, pk=pk)
            if action == 'recharge':
                data = checked(AmountForm(request.data))
                move_card(pk, Kind.RECHARGE, data['amount'], request.user, data.get('request_key') or uuid4())
            elif action == 'expiry':
                if card.is_deposit:
                    raise ValidationError('Customer deposits have no expiration date.')
                data = checked(ExpiryForm(request.data))
                GiftCard.objects.filter(pk=pk).update(expired_date=data['expired_date'])
            else:
                raise ValidationError('Unknown action.')
            card.refresh_from_db()
            return Response(card_data(card))
    except (IntegrityError, OperationalError) as exc:
        return handle_conflict(exc)


class GiftCardDetailPage(ProtectedAccountPageView):
    template_name = 'sale/gift_card.html'

    def has_permission(self):
        return allowed(self.request.user, 'view', True)

    def get_context_data(self, **kwargs):
        card = get_object_or_404(GiftCard, pk=kwargs['pk'])
        return super().get_context_data(
            **kwargs, card=card, row=card_data(card), page_label='Gift card', page_section='sale', sale_area='gift-cards',
            entries=card.entries.select_related('created_by').order_by('-pk'), currency=settings.INVENTORY_BASE_CURRENCY,
            can_change=allowed(self.request.user, 'change', True), recharge=AmountForm(),
            expiry=ExpiryForm(initial={'expired_date': card.expired_date}))


@login_required
@never_cache
@require_safe
def card_image(request, pk):
    require(request.user, 'view', True)
    card = get_object_or_404(GiftCard, pk=pk)
    canvas = Image.new('RGB', (1000, 600), '#123a34')
    draw = ImageDraw.Draw(canvas)
    fonts = settings.BASE_DIR / 'report/static/report/fonts'
    def label(x, y, value, size, color='#fff7e2', bold=False):
        font = ImageFont.truetype(str(fonts / ('NotoSans-SemiBold.ttf' if bold else 'NotoSans-Regular.ttf')), size)
        draw.text((x, y), value, fill=color, font=font)
    for radius in (130, 170, 210, 250):
        draw.ellipse((885-radius, 45-radius, 885+radius, 45+radius), outline='#487567', width=2)
    label(58, 42, 'eManager', 30)
    label(58, 125, 'Customer deposit' if card.is_deposit else 'Gift card', 88, bold=True)
    label(60, 300, ' '.join(card.card_no[i:i+4] for i in range(0, 16, 4)), 55)
    draw.line((60, 440, 940, 440), fill='#739485', width=2)
    label(60, 462, 'VALID THROUGH', 20)
    label(60, 501, 'No expiry' if card.is_deposit else card.expired_date.strftime('%d %b %Y'), 34)
    label(670, 462, 'BALANCE', 20)
    label(670, 510, f'{settings.INVENTORY_BASE_CURRENCY} {card.balance:,.2f}', 26)
    output = BytesIO()
    canvas.save(output, format='PNG')
    mode = 'attachment' if 'download' in request.GET else 'inline'
    return HttpResponse(output.getvalue(), content_type='image/png', headers={
        'Content-Disposition': f'{mode}; filename="gift-card-{card.card_no[-4:]}.png"'})

