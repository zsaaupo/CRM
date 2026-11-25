from decimal import Decimal
from uuid import uuid4

from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from account.page_views import ProtectedAccountPageView
from account.permissions import has_role_permissions
from common.view import BaseAPIMixin, BasePagination
from inventory.models import Product
from .counts import CountConflict, begin_count, finalize_count
from .models import StockCount, StockCountLine, WareHouse
from .scope import WarehouseScopeAPIMixin, has_warehouse_scope, scoped_queryset, warehouse_scope


class CountInput(serializers.Serializer):
    warehouse = serializers.IntegerField(min_value=1, max_value=2**63-1)
    request_key = serializers.UUIDField()
    full_count = serializers.BooleanField()
    products = serializers.ListField(child=serializers.IntegerField(min_value=1, max_value=2**63-1), default=list)
    note = serializers.CharField(max_length=250, allow_blank=True, default='')

    def validate(self, attrs):
        if attrs['full_count'] == bool(attrs['products']):
            raise ValidationError({'products': 'Select products only for a partial count.'})
        return attrs


class CountedLine(serializers.Serializer):
    product = serializers.IntegerField(min_value=1, max_value=2**63-1)
    quantity = serializers.DecimalField(min_value=Decimal('0'), max_digits=18, decimal_places=6)


class CountLineOutput(serializers.ModelSerializer):
    sku = serializers.CharField(source='product.sku')
    unit = serializers.CharField(source='unit.code')

    class Meta:
        model = StockCountLine
        fields = ('product', 'sku', 'unit', 'expected', 'counted')


class CountOutput(serializers.ModelSerializer):
    class Meta:
        model = StockCount
        fields = ('id', 'warehouse', 'full_count', 'note', 'created_at', 'create_by', 'finalized_at', 'modified_by')


class CountResource:
    @property
    def required_permissions(self):
        permissions = ('warehouse.view_stock', 'warehouse.view_stockcount')
        if self.request.method == 'POST':
            return (*permissions, 'warehouse.change_stockcount' if 'pk' in self.kwargs else 'warehouse.add_stockcount')
        return permissions

    def records(self):
        return scoped_queryset(StockCount.objects.select_related('warehouse'), warehouse_scope(self.request.user))

    def save_count(self, data):
        if 'pk' in self.kwargs:
            lines = CountedLine(many=True, data=data.get('lines'))
            lines.is_valid(raise_exception=True)
            quantities = {row['product']: row['quantity'] for row in lines.validated_data}
            if len(quantities) != len(lines.validated_data):
                raise ValidationError({'lines': 'Choose each product once.'})
            return finalize_count(self.request.user, self.kwargs['pk'], quantities)
        form = CountInput(data=data)
        form.is_valid(raise_exception=True)
        return begin_count(self.request.user, **form.validated_data)


class StockCountAPI(CountResource, WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    def get(self, request, pk=None):
        if pk is not None:
            count = get_object_or_404(self.records(), pk=pk)
            return Response({**CountOutput(count).data,
                             'lines': CountLineOutput(count.lines.select_related('product', 'unit'), many=True).data})
        pagination = BasePagination()
        records = pagination.paginate_queryset(self.records(), request)
        return pagination.get_paginated_response(CountOutput(records, many=True).data)

    def post(self, request, pk=None):
        return Response(CountOutput(self.save_count(request.data)).data)


class StockCountPage(CountResource, ProtectedAccountPageView):
    template_name = 'warehouse/count.html'

    def has_permission(self):
        return has_role_permissions(self.request.user, self.required_permissions) and has_warehouse_scope(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        if 'pk' in self.kwargs:
            count = get_object_or_404(self.records(), pk=self.kwargs['pk'])
            context.update(record=count, lines=count.lines.select_related('product', 'unit'),
                           can_finalize=has_role_permissions(user, ('warehouse.change_stockcount',)))
        else:
            branches = scoped_queryset(WareHouse.objects.filter(is_active=True), warehouse_scope(user), 'pk')
            context.update(records=Paginator(self.records(), 50).get_page(self.request.GET.get('page')),
                           warehouses=branches, request_key=uuid4(),
                           can_add=has_role_permissions(user, ('warehouse.add_stockcount',)),
                           products=Product.objects.order_by('sku').only('sku', 'name'))
        return context

    def post(self, request, **kwargs):
        if 'pk' in kwargs:
            data = {'lines': [{'product': key[4:], 'quantity': val} for key, val in request.POST.items()
                              if key.startswith('qty_')]}
        else:
            data = request.POST.dict()
            data['products'] = request.POST.getlist('products')
        try:
            record = self.save_count(data)
        except (ValidationError, CountConflict) as error:
            return self.render_to_response(self.get_context_data(errors=error.detail), status=error.status_code)
        return redirect('warehouse_pages:count-detail', pk=record.pk)
