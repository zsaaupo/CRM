from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef, Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import serializers, status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from common.serializers import AUDIT_FIELDS, CatalogSerializer
from common.view import BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView, BasePagination
from sale.returns import completed_orders
from warehouse.models import WareHouse
from warehouse.scope import (
    WarehouseScopeAPIMixin, WarehouseScopedSerializerMixin, lock_warehouse_scope, scoped_queryset, warehouse_scope,
)

from .models import (
    MIN_AMOUNT, AccountTransaction, AccountTransfer, Expense, ExpenseCategory, ExpensePayment, FinancialAccount,
    SalesPayment,
)
from .services import (
    ACCOUNT_VIEW, EXPENSE_COMPLETE, PAYMENT_VIEW, SALES_PAYMENT_VIEW, STATEMENT_VIEW, TRANSFER_VIEW, ExpenseCompleted,
    accounts_with_balances, available_payment_accounts, complete_expense, lock_expense, lock_rows, paid_amount,
    payment_account_options, post_transfer,
)
from .serializers import PaymentAccountField


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


class ExpenseSerializer(WarehouseScopedSerializerMixin, CatalogSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    currency = serializers.SerializerMethodField()
    has_payment_history = serializers.SerializerMethodField()
    payment_account = PaymentAccountField()
    payment_account_name = serializers.CharField(source='payment_account.name', read_only=True, default=None)

    class Meta:
        model = Expense
        fields = [*AUDIT_FIELDS, 'date', 'category', 'category_name', 'warehouse', 'warehouse_name', 'amount',
                  'currency', 'has_payment_history', 'payment_account', 'payment_account_name', 'is_completed',
                  'completed_at']
        read_only_fields = [*AUDIT_FIELDS, 'is_completed', 'completed_at']

    def validate(self, attrs):
        if self.instance and self.instance.is_completed:
            raise ExpenseCompleted()
        return super().validate(attrs)

    def get_currency(self, expense):
        return settings.INVENTORY_BASE_CURRENCY

    def get_has_payment_history(self, expense):
        if hasattr(expense, 'has_payment_history'):
            return expense.has_payment_history
        return expense.payments.exists()

    def to_representation(self, expense):
        data = super().to_representation(expense)
        paid = paid_amount(expense)
        outstanding = expense.amount - paid
        data.update(
            paid_amount=format(paid, '.4f'), outstanding_amount=format(outstanding, '.4f'),
            payment_status='Paid' if not outstanding else 'Partially paid' if paid else 'Unpaid',
        )
        return data

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
        amounts = queryset.prefetch_related(None).order_by().values_list('amount', flat=True).iterator()
        total = sum(amounts, Decimal('0.0000'))
        response = super().get_paginated_response(data)
        response.data.update(total_amount=format(total, 'f'), currency=settings.INVENTORY_BASE_CURRENCY)
        return response


class ExpenseResource(WarehouseScopeAPIMixin):
    queryset = Expense.objects.select_related('category', 'warehouse', 'payment_account').annotate(
        has_payment_history=Exists(ExpensePayment.objects.filter(expense_id=OuterRef('pk'))),
    ).prefetch_related(Prefetch(
        'payments',
        queryset=ExpensePayment.objects.only('expense_id').prefetch_related(Prefetch(
            'transactions', queryset=AccountTransaction.objects.only('expense_payment_id', 'direction', 'amount'),
            to_attr='ledger_rows',
        )),
        to_attr='ledger_payments',
    ))
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
        expense = lock_expense(instance.pk, scope)
        if expense.is_completed:
            raise ExpenseCompleted()
        super().perform_destroy(expense)


class ExpenseCompleteSerializer(serializers.Serializer):
    expense_id = serializers.IntegerField(min_value=1, max_value=2**63 - 1)
    is_completed = serializers.ChoiceField(choices=[True])


@method_decorator(never_cache, name='dispatch')
class ExpenseCompleteAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = EXPENSE_COMPLETE
    http_method_names = ['post', 'options']

    def post(self, request):
        data = ExpenseCompleteSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        expense, completed_now = complete_expense(request.user, data.validated_data['expense_id'])
        return Response({
            'message': 'Expense completed.' if completed_now else 'Expense was already completed.',
            'completed_now': completed_now,
            'expense': ExpenseSerializer(expense, context={'request': request}).data,
        })


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
            'payment_account': payment_account_options(available_payment_accounts(request.warehouse_scope)),
        })


class FinancialAccountSerializer(WarehouseScopedSerializerMixin, CatalogSerializer):
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True, default='Shared')
    type_label = serializers.CharField(source='get_type_display', read_only=True)
    balance = serializers.SerializerMethodField()

    class Meta:
        model = FinancialAccount
        fields = [*AUDIT_FIELDS, 'name', 'type', 'type_label', 'warehouse', 'warehouse_name', 'currency',
                  'opening_balance', 'balance', 'is_active']
        read_only_fields = [*AUDIT_FIELDS, 'currency']

    def get_balance(self, account):
        if not hasattr(account, 'balance'):
            account.balance = accounts_with_balances(FinancialAccount.objects.filter(pk=account.pk)).values_list(
                'balance', flat=True,
            ).get()
        return format(account.balance, '.4f')

    def update(self, instance, validated_data):
        scope = warehouse_scope(self.context['request'].user)
        self.instance = current = get_object_or_404(lock_rows(scoped_queryset(
            FinancialAccount.objects.filter(pk=instance.pk), scope,
        )))
        return super().update(current, self.validate(validated_data))


class FinancialAccountResource(WarehouseScopeAPIMixin):
    queryset = accounts_with_balances(FinancialAccount.objects.select_related('warehouse'))
    serializer_class = FinancialAccountSerializer


class FinancialAccountListCreateAPIView(FinancialAccountResource, BaseListCreateAPIView):
    response_key = 'financial_account'
    search_fields = ['name', 'warehouse__name']


class FinancialAccountDetailAPIView(FinancialAccountResource, BaseDetailAPIView):
    @transaction.atomic
    def perform_destroy(self, instance):
        scope = lock_warehouse_scope(self.request.user)
        self.check_permissions(self.request)
        account = get_object_or_404(lock_rows(scoped_queryset(FinancialAccount.objects.filter(pk=instance.pk), scope)))
        super().perform_destroy(account)


class AccountTransactionSerializer(serializers.ModelSerializer):
    source = serializers.SerializerMethodField()
    reference = serializers.SerializerMethodField()
    direction_label = serializers.CharField(source='get_direction_display')
    currency = serializers.CharField(source='account.currency')

    class Meta:
        model = AccountTransaction
        fields = [*AUDIT_FIELDS, 'date', 'account', 'direction', 'direction_label', 'amount', 'balance_after',
                  'currency', 'source', 'reference', 'transfer', 'expense_payment', 'sales_payment']
        read_only_fields = fields

    def get_source(self, row):
        if row.sales_payment_id:
            return f'Sales payment {row.sales_payment.number}'
        return f'Transfer {row.transfer.number}' if row.transfer_id else f'Expense payment {row.expense_payment.number}'

    def get_reference(self, row):
        return (row.transfer or row.expense_payment or row.sales_payment).reference


class AccountStatementAPIView(WarehouseScopeAPIMixin, BaseListAPIView):
    queryset = AccountTransaction.objects.select_related('account', 'transfer', 'expense_payment', 'sales_payment')
    serializer_class = AccountTransactionSerializer
    required_permissions = STATEMENT_VIEW
    warehouse_lookup = 'account__warehouse_id'
    ordering = ['-pk']
    search_fields = ['transfer__reference', 'expense_payment__reference', 'sales_payment__reference']

    def get_queryset(self):
        self.account = get_object_or_404(scoped_queryset(
            accounts_with_balances(FinancialAccount.objects.select_related('warehouse')), self.request.warehouse_scope,
        ), pk=self.kwargs['pk'])
        return super().get_queryset().filter(account_id=self.account.pk)

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        response.data['account'] = FinancialAccountSerializer(self.account).data
        return response


class PostingSerializer(serializers.Serializer):
    date = serializers.DateField(default=timezone.localdate)
    amount = serializers.DecimalField(max_digits=15, decimal_places=4, min_value=MIN_AMOUNT)
    reference = serializers.CharField(max_length=150, allow_blank=True, default='')
    request_key = serializers.UUIDField()


class TransferPostingSerializer(PostingSerializer):
    source_account = serializers.IntegerField(min_value=1, max_value=2**63 - 1)
    destination_account = serializers.IntegerField(min_value=1, max_value=2**63 - 1)


class AccountTransferSerializer(serializers.ModelSerializer):
    transfer_number = serializers.CharField(source='number')
    source_account_name = serializers.CharField(source='source_account.name')
    destination_account_name = serializers.CharField(source='destination_account.name')
    currency = serializers.CharField(source='source_account.currency')

    class Meta:
        model = AccountTransfer
        fields = [*AUDIT_FIELDS, 'transfer_number', 'source_account', 'source_account_name', 'destination_account',
                  'destination_account_name', 'currency', 'date', 'amount', 'reference']
        read_only_fields = fields


class AccountTransferListCreateAPIView(WarehouseScopeAPIMixin, BaseListCreateAPIView):
    queryset = AccountTransfer.objects.select_related('source_account', 'destination_account')
    serializer_class = AccountTransferSerializer
    warehouse_lookup = 'source_account__warehouse_id'
    required_permissions = {'GET': TRANSFER_VIEW, 'POST': (*TRANSFER_VIEW, 'accounting.add_accounttransfer')}
    search_fields = ['reference', 'source_account__name', 'destination_account__name']
    ordering = ['-pk']

    def get_queryset(self):
        # Both accounts must be visible before disclosing the transfer details.
        return scoped_queryset(super().get_queryset(), self.request.warehouse_scope, 'destination_account__warehouse_id')

    def create(self, request, *args, **kwargs):
        data = TransferPostingSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        record, created = post_transfer(request.user, **data.validated_data)
        return Response({
            'message': 'Transfer posted successfully.',
            'account_transfer': self.get_serializer(self.queryset.get(pk=record.pk)).data,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class PaymentSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source='number')
    account_name = serializers.CharField(source='account.name')
    currency = serializers.CharField(source='account.currency')
    method_label = serializers.CharField(source='get_method_display')
    payment_fields = ['account', 'account_name', 'date', 'amount', 'currency', 'method', 'method_label', 'reference']


class ExpensePaymentSerializer(PaymentSerializer):

    class Meta:
        model = ExpensePayment
        fields = [*AUDIT_FIELDS, 'name', 'expense', *PaymentSerializer.payment_fields]
        read_only_fields = fields


class ExpensePaymentListAPIView(WarehouseScopeAPIMixin, BaseListAPIView):
    queryset = ExpensePayment.objects.select_related('account')
    serializer_class = ExpensePaymentSerializer
    warehouse_lookup = 'account__warehouse_id'
    required_permissions = PAYMENT_VIEW
    search_fields = ['reference', 'account__name']
    ordering = ['-pk']

    def get_queryset(self):
        expense = get_object_or_404(scoped_queryset(Expense.objects.all(), self.request.warehouse_scope),
                                    pk=self.kwargs['expense_pk'])
        return super().get_queryset().filter(expense=expense)

class SalesPaymentSerializer(PaymentSerializer):
    class Meta:
        model = SalesPayment
        fields = [*AUDIT_FIELDS, 'name', 'sales_order', *PaymentSerializer.payment_fields]
        read_only_fields = fields


class SalesPaymentListAPIView(WarehouseScopeAPIMixin, BaseListAPIView):
    queryset = SalesPayment.objects.select_related('account')
    serializer_class = SalesPaymentSerializer
    warehouse_lookup = 'account__warehouse_id'
    required_permissions = SALES_PAYMENT_VIEW
    search_fields = ['reference', 'account__name']
    ordering = ['-pk']

    def get_queryset(self):
        order = get_object_or_404(completed_orders(self.request.warehouse_scope), pk=self.kwargs['sales_order_pk'])
        return super().get_queryset().filter(sales_order=order)
