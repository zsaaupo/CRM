from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.paginator import Paginator
from django.db import OperationalError, transaction
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from account.page_views import ProtectedAccountPageView
from account.permissions import has_role_permissions
from accounting.services import lock_rows
from common.view import BaseAPIMixin, BasePagination
from inventory.models import Product
from .models import Stock, StockAdjustment, StockMovement, WareHouse
from .movements import post_valued_movements
from .scope import WarehouseScopeAPIMixin, has_warehouse_scope, lock_warehouse_scope, scoped_queryset, warehouse_scope
from .services import MAX_STOCK_QUANTITY


class AdjustmentBusy(APIException):
    status_code = 409
    default_detail = 'Stock is being updated. Retry this adjustment with the same request key.'


class AdjustmentInput(serializers.Serializer):
    request_key = serializers.UUIDField()
    warehouse = serializers.IntegerField(min_value=1, max_value=2**63-1)
    product = serializers.IntegerField(min_value=1, max_value=2**63-1)
    action = serializers.ChoiceField(choices=['add', 'subtract'])
    quantity = serializers.DecimalField(max_digits=18, decimal_places=6, min_value=Decimal('0.000001'))
    declared_unit_cost = serializers.DecimalField(max_digits=18, decimal_places=8, min_value=0,
                                                  required=False, allow_null=True)
    reference = serializers.CharField(max_length=100, allow_blank=True, default='')
    reason = serializers.CharField(max_length=250)


class AdjustmentOutput(serializers.ModelSerializer):
    sku = serializers.CharField(source='product.sku')
    unit_code = serializers.CharField(source='unit.code')

    class Meta:
        model = StockAdjustment
        fields = ('id', 'request_key', 'warehouse', 'product', 'sku', 'unit_code', 'quantity',
                  'declared_unit_cost', 'reference', 'reason', 'created_at', 'create_by')


@transaction.atomic
def post_adjustment(actor, *, request_key, warehouse, product, action, quantity, reference, reason, declared_unit_cost=None):
    scope = lock_warehouse_scope(actor)
    if not has_role_permissions(actor, ('warehouse.view_stock', 'warehouse.view_stockadjustment', 'warehouse.add_stockadjustment')):
        raise PermissionDenied()
    branch = get_object_or_404(lock_rows(scoped_queryset(WareHouse.objects.filter(pk=warehouse), scope, 'pk')))
    item = get_object_or_404(Product.objects.select_for_update(), pk=product)
    delta = quantity if action == 'add' else -quantity
    username = actor.get_username()
    values = dict(warehouse_id=branch.pk, product_id=item.pk, quantity=delta, reason=reason,
                  declared_unit_cost=declared_unit_cost, reference=reference)
    record, created = StockAdjustment.objects.get_or_create(request_key=request_key,
        defaults=dict(values, unit_id=item.unit_id, create_by=username, modified_by=username))
    if not created:
        if any(getattr(record, key) != value for key, value in values.items()):
            raise ValidationError({'request_key': 'This request key was used for another adjustment.'})
        return record, False
    if not branch.is_active:
        raise ValidationError({'warehouse': 'Choose an active warehouse.'})
    if delta < 0 and declared_unit_cost is not None:
        raise ValidationError({'declared_unit_cost': 'Stock reductions use the current average cost.'})
    stock, _ = Stock.objects.select_for_update().get_or_create(warehouse=branch, product=item,
        defaults={'create_by': username, 'modified_by': username})
    balance = stock.quantity + delta
    if balance < 0 or balance > MAX_STOCK_QUANTITY:
        raise ValidationError({'quantity': 'The resulting stock would be outside the supported quantity range.'})
    stock.quantity = balance
    stock.modified_by = username
    stock.save(update_fields=['quantity', 'modified_by', 'updated_at'])
    stock.refresh_from_db(fields=['quantity'])
    if stock.quantity != balance:
        raise ValidationError({'quantity': 'The resulting quantity exceeds database precision.'})
    entry = StockMovement(warehouse=branch, product=item, unit_id=record.unit_id, quantity=delta,
        kind=StockMovement.Kind.ADJUSTMENT, unit_cost_base=declared_unit_cost if declared_unit_cost is not None else stock.average_cost_base,
        source_id=record.pk, posting_key=f'adjustment/{record.pk}', posted_by=username, business_date=timezone.localdate())
    post_valued_movements(branch.pk, [entry])
    return record, True


class AdjustmentResource:
    @property
    def required_permissions(self):
        permissions = ('warehouse.view_stock', 'warehouse.view_stockadjustment')
        return (*permissions, 'warehouse.add_stockadjustment') if self.request.method == 'POST' else permissions

    def records(self):
        return scoped_queryset(StockAdjustment.objects.select_related('warehouse', 'product', 'unit'),
                               warehouse_scope(self.request.user))

    def post_record(self, data):
        form = AdjustmentInput(data=data)
        form.is_valid(raise_exception=True)
        try:
            return post_adjustment(self.request.user, **form.validated_data)
        except OperationalError as exc:
            if 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
                raise AdjustmentBusy() from exc
            raise


class AdjustmentAPI(AdjustmentResource, WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    def get(self, request):
        page = BasePagination()
        return page.get_paginated_response(AdjustmentOutput(page.paginate_queryset(self.records(), request), many=True).data)

    def post(self, request):
        record, created = self.post_record(request.data)
        return Response(AdjustmentOutput(record).data, status=201 if created else 200)


class AdjustmentPage(AdjustmentResource, ProtectedAccountPageView):
    template_name = 'warehouse/adjustments.html'

    def has_permission(self):
        return has_role_permissions(self.request.user, self.required_permissions) and has_warehouse_scope(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context.update(page_section='warehouses', records=Paginator(self.records(), 50).get_page(self.request.GET.get('page')),
            warehouses=scoped_queryset(WareHouse.objects.filter(is_active=True), warehouse_scope(user), 'pk'),
            products=Product.objects.select_related('unit').order_by('sku'), currency=settings.INVENTORY_BASE_CURRENCY,
            request_key=uuid4(), can_add=has_role_permissions(user, ('warehouse.add_stockadjustment',)))
        return context

    def post(self, request):
        try:
            self.post_record(request.POST)
        except (ValidationError, AdjustmentBusy) as exc:
            return self.render_to_response(self.get_context_data(errors=exc.detail), status=exc.status_code)
        return redirect('warehouse_pages:adjustments')
