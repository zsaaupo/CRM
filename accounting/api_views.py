from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from common.serializers import AUDIT_FIELDS, CatalogSerializer
from common.view import BaseAPIMixin, BaseDetailAPIView, BaseListCreateAPIView, BasePagination
from warehouse.models import WareHouse
from warehouse.scope import (
    WarehouseScopeAPIMixin, WarehouseScopedSerializerMixin, lock_warehouse_scope, scoped_queryset, warehouse_scope,
)

from .models import Expense, ExpenseCategory


class ExpenseCategorySerializer(CatalogSerializer):
    class Meta:
        model = ExpenseCategory
        fields = [*AUDIT_FIELDS, 'name', 'is_active']
        read_only_fields = AUDIT_FIELDS


class ExpenseCategoryResource:
    queryset = ExpenseCategory.objects.all()
    serializer_class = ExpenseCategorySerializer


class ExpenseCategoryListCreateAPIView(ExpenseCategoryResource, BaseListCreateAPIView):
    response_key = 'expense_category'


class ExpenseCategoryDetailAPIView(ExpenseCategoryResource, BaseDetailAPIView):
    pass


def lock_expense(pk, scope):
    return get_object_or_404(scoped_queryset(Expense.objects.select_for_update(), scope), pk=pk)


class ExpenseSerializer(WarehouseScopedSerializerMixin, CatalogSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    currency = serializers.SerializerMethodField()

    class Meta:
        model = Expense
        fields = [*AUDIT_FIELDS, 'date', 'category', 'category_name', 'warehouse', 'warehouse_name', 'amount', 'currency']
        read_only_fields = AUDIT_FIELDS

    def get_currency(self, expense):
        return settings.INVENTORY_BASE_CURRENCY

    def update(self, instance, validated_data):
        # The view locks the actor's warehouse assignment before this write.
        self.instance = current = lock_expense(instance.pk, warehouse_scope(self.context['request'].user))
        return super().update(current, self.validate(validated_data))


class ExpenseFilterSerializer(serializers.Serializer):
    from_date = serializers.DateField(required=False)
    to_date = serializers.DateField(required=False)
    category = serializers.IntegerField(required=False, min_value=1, max_value=2**63 - 1)
    warehouse = serializers.IntegerField(required=False, min_value=1, max_value=2**63 - 1)

    def validate(self, attrs):
        start = attrs.get('from_date')
        end = attrs.get('to_date')
        if start and end and start > end:
            raise serializers.ValidationError({'to_date': 'The end date must be on or after the start date.'})
        return attrs


class ExpensePagination(BasePagination):
    def get_paginated_response(self, data):
        queryset = self.page.paginator.object_list
        amounts = queryset.order_by().values_list('amount', flat=True).iterator()
        total = sum(amounts, Decimal('0.0000'))
        response = super().get_paginated_response(data)
        response.data.update(total_amount=format(total, 'f'), currency=settings.INVENTORY_BASE_CURRENCY)
        return response


class ExpenseResource(WarehouseScopeAPIMixin):
    queryset = Expense.objects.select_related('category', 'warehouse')
    serializer_class = ExpenseSerializer


class ExpenseListCreateAPIView(ExpenseResource, BaseListCreateAPIView):
    response_key = 'expense'
    search_fields = ['category__name', 'warehouse__name']
    ordering = ['-date', '-pk']
    pagination_class = ExpensePagination

    def get_queryset(self):
        queryset = super().get_queryset()
        filters = ExpenseFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        lookups = {'from_date': 'date__gte', 'to_date': 'date__lte', 'category': 'category_id', 'warehouse': 'warehouse_id'}
        return queryset.filter(**{lookups[name]: value for name, value in filters.validated_data.items()})

    @transaction.atomic
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)


class ExpenseDetailAPIView(ExpenseResource, BaseDetailAPIView):
    @transaction.atomic
    def perform_destroy(self, instance):
        scope = lock_warehouse_scope(self.request.user)
        self.check_permissions(self.request)
        super().perform_destroy(lock_expense(instance.pk, scope))


@method_decorator(never_cache, name='dispatch')
class ExpenseOptionsAPIView(ExpenseResource, BaseAPIMixin, GenericAPIView):
    http_method_names = ['get', 'head', 'options']
    edit = False

    @property
    def required_permissions(self):
        return ('accounting.view_expense', 'accounting.change_expense') if self.edit else ('accounting.add_expense',)

    def get(self, request, pk=None):
        expense = get_object_or_404(self.get_queryset(), pk=pk) if self.edit else None
        category_filter = Q(is_active=True)
        warehouse_filter = Q(is_active=True)
        if expense:
            category_filter |= Q(pk=expense.category_id)
            warehouse_filter |= Q(pk=expense.warehouse_id)
        categories = ExpenseCategory.objects.filter(category_filter)
        warehouses = scoped_queryset(WareHouse.objects.all(), request.warehouse_scope, 'pk')
        warehouses = warehouses.filter(warehouse_filter).order_by('name', 'pk')
        return Response({
            'category': [{'value': row.pk, 'label': row.name} for row in categories],
            'warehouse': [{'value': row.pk, 'label': row.name} for row in warehouses],
        })
