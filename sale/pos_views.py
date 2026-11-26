from django.conf import settings
from django.core.paginator import Paginator
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import never_cache
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from account.page_views import ProtectedAccountPageView
from account.permissions import has_role_permissions
from accounting.services import available_payment_accounts
from inventory.models import Brand, ProductCategory
from people.models import Party
from warehouse.models import WareHouse
from warehouse.scope import WarehouseScopePageMixin, lock_warehouse_scope, scoped_queryset, warehouse_scope
from .models import GiftCard, POSTicket
from .pos import (POS_PERMS, CartInput, CheckoutInput, TicketInput, price, price_cart, products, save_ticket,
                  validate_input, warehouse_for)
from .services import SaleBusy


class POSPage(WarehouseScopePageMixin, ProtectedAccountPageView):
    permission_required = POS_PERMS
    template_name = 'sale/pos.html'
    extra_context = {'page_section': 'sale', 'sale_area': 'pos', 'page_label': 'Point of sale'}

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        scope = warehouse_scope(self.request.user)
        ctx.update(warehouses=scoped_queryset(WareHouse.objects.filter(is_active=True), scope, 'pk'),
                   accounts=available_payment_accounts(scope), categories=ProductCategory.objects.filter(is_active=True),
                   brands=Brand.objects.filter(is_active=True), currency=settings.INVENTORY_BASE_CURRENCY,
                   today=timezone.localdate(), can_add_customer=self.request.user.has_perm('people.add_party'))
        return ctx


def pos_scope(request):
    if not has_role_permissions(request.user, POS_PERMS):
        raise PermissionDenied()
    return warehouse_scope(request.user)


def ticket_info(ticket):
    return dict(key=str(ticket.key), revision=ticket.revision, warehouse=ticket.warehouse_id,
                status='completed' if ticket.order_id else 'cancelled' if ticket.cancelled else 'draft',
                order=ticket.order_id, number=ticket.receipt.get('number'), updated=ticket.updated_at)


@never_cache
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def catalog(request):
    scope = pos_scope(request)
    p = request.query_params
    warehouse = warehouse_for(scope, serializers.IntegerField(min_value=1).run_validation(p.get('warehouse')))
    date = serializers.DateField().run_validation(p.get('date', timezone.localdate()))
    rows = products().filter(stocks__warehouse=warehouse, stocks__quantity__gt=0).select_related('unit')
    scan = p.get('scan', '').strip()
    if scan:
        exact = rows.filter(barcode=scan)
        rows = exact if exact.exists() else rows.filter(sku__iexact=scan)
    else:
        search = p.get('q', '').strip()
        rows = rows.filter(Q(name__icontains=search) | Q(sku__icontains=search))
        for field in ('category', 'brand'):
            if p.get(field):
                rows = rows.filter(**{field: serializers.IntegerField(min_value=1).run_validation(p[field])})
        if p.get('featured') == '1':
            rows = rows.filter(is_featured=True)
    page = Paginator(rows.order_by('name', 'pk'), 24).get_page(p.get('page'))
    return Response(dict(results=[dict(id=r.pk, name=r.name, sku=r.sku, unit=r.unit.code, price=str(price(r, date)),
                                       image=r.image.url if r.image else '') for r in page], page=page.number, pages=page.paginator.num_pages))


@never_cache
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def customers(request):
    pos_scope(request)
    rows = Party.objects.filter(is_customer=True, is_active=True)
    if request.query_params.get('id'):
        rows = rows.filter(pk=serializers.IntegerField(min_value=1).run_validation(request.query_params['id']))
    else:
        q = request.query_params.get('q', '').strip()
        rows = rows.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(phone__icontains=q))
    return Response(list(rows.order_by('name', 'pk').values('id', 'code', 'name')[:30]))


@never_cache
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def quote(request):
    scope = pos_scope(request)
    cart = validate_input(CartInput, request.data)
    _, customer, _, summary = price_cart(cart, scope)
    balance = GiftCard.objects.filter(is_deposit=True, is_active=True, customer=customer).first() if customer else None
    summary['deposit_balance'] = str(balance.balance) if balance else '0.00'
    return Response(summary)


@never_cache
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def write_ticket(request, checkout):
    pos_scope(request)
    data = validate_input(CheckoutInput if checkout else TicketInput, request.data)
    try:
        ticket = save_ticket(request.user, data, checkout=checkout)
    except (OperationalError, IntegrityError) as exc:
        if isinstance(exc, IntegrityError) or 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
            raise SaleBusy() from exc
        raise
    return Response({**ticket_info(ticket), 'receipt': ticket.receipt})


@never_cache
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def history(request):
    scope = pos_scope(request)
    warehouse = warehouse_for(scope, serializers.IntegerField(min_value=1).run_validation(request.query_params.get('warehouse')))
    rows = POSTicket.objects.filter(warehouse=warehouse, cashier=request.user, cancelled=False).order_by('-updated_at')
    page = Paginator(rows.order_by('-updated_at', 'key'), 20).get_page(request.query_params.get('page'))
    return Response(dict(results=[ticket_info(ticket) for ticket in page], page=page.number, pages=page.paginator.num_pages))


@never_cache
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def ticket_detail(request, key):
    scope = pos_scope(request)
    rows = POSTicket.objects.filter(cashier=request.user)
    if request.method == 'GET':
        ticket = get_object_or_404(scoped_queryset(rows, scope), key=key)
        return Response({**ticket_info(ticket), 'cart': ticket.cart, 'receipt': ticket.receipt})
    with transaction.atomic():
        scope = lock_warehouse_scope(request.user)
        if not has_role_permissions(request.user, POS_PERMS):
            raise PermissionDenied()
        revision = serializers.IntegerField(min_value=1).run_validation(request.data.get('revision'))
        if not scoped_queryset(rows, scope).filter(key=key, order__isnull=True, revision=revision).update(cancelled=True):
            raise SaleBusy('Draft changed. Reload recent transactions.')
    return Response({'cancelled': True})
