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
from rest_framework.utils.urls import replace_query_param
from rest_framework.views import APIView

from common.serializers import AUDIT_FIELDS, CatalogSerializer
from common.view import BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView, BasePagination
from purchase.returns import received_orders
from sale.returns import completed_orders
from warehouse.models import WareHouse
from warehouse.scope import (
    WarehouseScopeAPIMixin, WarehouseScopedSerializerMixin, lock_warehouse_scope, scoped_queryset, warehouse_scope,
)

from .models import (
    MIN_AMOUNT, AccountTransaction, AccountTransfer, Expense, ExpenseCategory, ExpensePayment, FinancialAccount,
    Income, IncomeCategory, IncomePayment, PaymentMethod, PurchasePayment, SalesPayment, TransferType,
)
from .services import (
    ACCOUNT_VIEW, EXPENSE_COMPLETE, PAYMENT_VIEW, PURCHASE_PAYMENT_VIEW, SALES_PAYMENT_VIEW, STATEMENT_VIEW,
    TRANSFER_VIEW, ExpenseCompleted, INCOME_PAYMENT_VIEW, IncomeCompleted, accounts_with_balances,
    available_payment_accounts, complete_income_expense, confirm_clear_due, lock_income_expense, lock_rows,
    paid_amount, party_due_summary, payment_account_options, post_purchase_payment, post_sales_payment,
    post_transfer, preview_clear_due, reverse_payment,
)
from .serializers import PaymentAccountField


class ExpenseCategorySerializer(CatalogSerializer):
    class Meta:
        model = ExpenseCategory
        fields = [*AUDIT_FIELDS, 'name', 'is_active']
        read_only_fields = AUDIT_FIELDS


class IncomeCategorySerializer(ExpenseCategorySerializer):
    class Meta(ExpenseCategorySerializer.Meta):
        model = IncomeCategory


class ExpenseCategoryResource:
    queryset = ExpenseCategory.objects.all()
    serializer_class = ExpenseCategorySerializer


class ExpenseCategoryListCreateAPIView(ExpenseCategoryResource, BaseListCreateAPIView):
    response_key = 'expense_category'


class ExpenseCategoryDetailAPIView(ExpenseCategoryResource, BaseDetailAPIView):
    pass


class ExpenseSerializer(WarehouseScopedSerializerMixin, CatalogSerializer):
    completed_error = ExpenseCompleted
    category_name = serializers.CharField(source='category.name', read_only=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    currency = serializers.SerializerMethodField()
    has_payment_history = serializers.SerializerMethodField()
    payment_account = PaymentAccountField()
    payment_account_name = serializers.CharField(source='payment_account.name', read_only=True, default=None)

    class Meta:
        model = Expense
        fields = [*AUDIT_FIELDS, 'date', 'category', 'category_name', 'warehouse', 'warehouse_name', 'amount',
                  'currency', 'has_payment_history', 'payment_account', 'payment_account_name', 'note', 'reference',
                  'is_completed', 'completed_at']
        read_only_fields = [*AUDIT_FIELDS, 'is_completed', 'completed_at']

    def validate(self, attrs):
        if self.instance and self.instance.is_completed:
            raise self.completed_error()
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
        self.instance = current = lock_income_expense(type(instance), instance.pk, warehouse_scope(self.context['request'].user))
        return super().update(current, self.validate(validated_data))


class IncomeSerializer(ExpenseSerializer):
    completed_error = IncomeCompleted

    class Meta(ExpenseSerializer.Meta):
        model = Income


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


def income_expense_queryset(model, payment_model):
    key = model._meta.model_name
    payments = payment_model.objects.only(f'{key}_id').prefetch_related(Prefetch(
        'transactions', queryset=AccountTransaction.objects.only(f'{key}_payment_id', 'direction', 'amount'),
        to_attr='ledger_rows',
    ))
    return model.objects.select_related('category', 'warehouse', 'payment_account').annotate(
        has_payment_history=Exists(payment_model.objects.filter(**{f'{key}_id': OuterRef('pk')})),
    ).prefetch_related(Prefetch('payments', queryset=payments, to_attr='ledger_payments'))


class ExpenseResource(WarehouseScopeAPIMixin):
    queryset = income_expense_queryset(Expense, ExpensePayment)
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
        expense = lock_income_expense(type(instance), instance.pk, scope)
        if expense.is_completed:
            raise ExpenseCompleted()
        super().perform_destroy(expense)


class ExpenseCompleteSerializer(serializers.Serializer):
    expense_id = serializers.IntegerField(min_value=1, max_value=2**63 - 1)
    is_completed = serializers.ChoiceField(choices=[True])


class IncomeCompleteSerializer(serializers.Serializer):
    income_id = serializers.IntegerField(min_value=1, max_value=2**63 - 1)
    is_completed = serializers.ChoiceField(choices=[True])


@method_decorator(never_cache, name='dispatch')
class ExpenseCompleteAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = EXPENSE_COMPLETE
    request_serializer = ExpenseCompleteSerializer
    record_serializer = ExpenseSerializer
    http_method_names = ['post', 'options']

    def post(self, request):
        model = self.record_serializer.Meta.model
        key = model._meta.model_name
        data = self.request_serializer(data=request.data)
        data.is_valid(raise_exception=True)
        record, completed_now = complete_income_expense(request.user, model, data.validated_data[f'{key}_id'])
        return Response({
            'message': f'{key.capitalize()} completed.' if completed_now else f'{key.capitalize()} was already completed.',
            'completed_now': completed_now,
            key: self.record_serializer(record, context={'request': request}).data,
        })


@method_decorator(never_cache, name='dispatch')
class ExpenseOptionsAPIView(ExpenseResource, BaseAPIMixin, GenericAPIView):
    http_method_names = ['get', 'head', 'options']
    edit = False

    @property
    def required_permissions(self):
        key = self.queryset.model._meta.model_name
        return (f'accounting.view_{key}', f'accounting.change_{key}') if self.edit else (f'accounting.add_{key}',)

    def get(self, request, pk=None):
        expense = get_object_or_404(self.get_queryset(), pk=pk) if self.edit else None
        category_filter = Q(is_active=True)
        warehouse_filter = Q(is_active=True)
        if expense:
            category_filter |= Q(pk=expense.category_id)
            warehouse_filter |= Q(pk=expense.warehouse_id)
        category_model = self.queryset.model._meta.get_field('category').related_model
        categories = category_model.objects.filter(category_filter)
        warehouses = scoped_queryset(WareHouse.objects.all(), request.warehouse_scope, 'pk')
        warehouses = warehouses.filter(warehouse_filter).order_by('name', 'pk')
        return Response({
            'category': [{'value': row.pk, 'label': row.name} for row in categories],
            'warehouse': [{'value': row.pk, 'label': row.name} for row in warehouses],
            'payment_account': payment_account_options(available_payment_accounts(request.warehouse_scope)),
        })


class IncomeCategoryListCreateAPIView(ExpenseCategoryListCreateAPIView):
    queryset = IncomeCategory.objects.all()
    serializer_class = IncomeCategorySerializer
    response_key = 'income_category'


class IncomeCategoryDetailAPIView(ExpenseCategoryDetailAPIView):
    queryset = IncomeCategory.objects.all()
    serializer_class = IncomeCategorySerializer


class IncomeResource:
    queryset = income_expense_queryset(Income, IncomePayment)
    serializer_class = IncomeSerializer


class IncomeListCreateAPIView(IncomeResource, ExpenseListCreateAPIView):
    response_key = 'income'


class IncomeDetailAPIView(IncomeResource, ExpenseDetailAPIView):
    pass


class IncomeOptionsAPIView(IncomeResource, ExpenseOptionsAPIView):
    pass


class IncomeCompleteAPIView(ExpenseCompleteAPIView):
    required_permissions = ('accounting.change_income',)
    request_serializer = IncomeCompleteSerializer
    record_serializer = IncomeSerializer


class FinancialAccountSerializer(WarehouseScopedSerializerMixin, CatalogSerializer):
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True, default='Shared')
    type_label = serializers.CharField(source='get_type_display', read_only=True)
    balance = serializers.SerializerMethodField()

    class Meta:
        model = FinancialAccount
        fields = [*AUDIT_FIELDS, 'name', 'type', 'type_label', 'warehouse', 'warehouse_name', 'currency',
                  'opening_balance', 'account_number', 'account_holder', 'bank_name', 'is_default', 'note',
                  'balance', 'is_active']
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
                  'currency', 'source', 'reference', 'transfer', 'expense_payment', 'income_payment', 'sales_payment', 'purchase_payment']
        read_only_fields = fields

    def get_source(self, row):
        if row.income_payment_id:
            return f'Income payment {row.income_payment.number}'
        if row.purchase_payment_id:
            return f'{row.purchase_payment.type_label} {row.purchase_payment.number}'
        if row.sales_payment_id:
            return f'{row.sales_payment.type_label} {row.sales_payment.number}'
        if row.transfer_id:
            return f'Transfer {row.transfer.number}'
        if row.expense_payment_id:
            return f'Expense payment {row.expense_payment.number}'
        return f'{row.get_source_kind_display()} #{row.source_ref_id}'

    def get_reference(self, row):
        linked = row.transfer or row.expense_payment or row.income_payment or row.sales_payment or row.purchase_payment
        return linked.reference if linked is not None else row.source_reference


class AccountTransactionListAPIView(WarehouseScopeAPIMixin, BaseListAPIView):
    queryset = AccountTransaction.objects.select_related(
        'account', 'transfer', 'expense_payment', 'income_payment', 'sales_payment', 'purchase_payment',
    )
    serializer_class = AccountTransactionSerializer
    required_permissions = STATEMENT_VIEW
    warehouse_lookup = 'account__warehouse_id'
    ordering = ['-pk']
    search_fields = ['transfer__reference', 'expense_payment__reference', 'income_payment__reference', 'sales_payment__reference',
                     'purchase_payment__reference']


class AccountStatementAPIView(AccountTransactionListAPIView):
    def get_queryset(self):
        self.account = get_object_or_404(scoped_queryset(
            accounts_with_balances(FinancialAccount.objects.select_related('warehouse')), self.request.warehouse_scope,
        ), pk=self.kwargs['pk'])
        return super().get_queryset().filter(account_id=self.account.pk)

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        response.data['account'] = FinancialAccountSerializer(self.account).data
        return response


class LedgerRequestSerializer(serializers.Serializer):
    account = serializers.IntegerField(min_value=1, max_value=2**63 - 1)
    start_date = serializers.DateField()
    end_date = serializers.DateField()

    def validate(self, data):
        if data['start_date'] > data['end_date']:
            raise serializers.ValidationError({'end_date': 'Choose an end date on or after the start date.'})
        return data


class LedgerEntrySerializer(AccountTransactionSerializer):
    running_balance = serializers.CharField(read_only=True)

    class Meta(AccountTransactionSerializer.Meta):
        fields = ['id', 'date', 'source', 'reference', 'direction', 'amount', 'running_balance']
        read_only_fields = fields

    def get_source(self, row):
        if row.transfer_id:
            return f'{row.transfer.get_transfer_type_display()} {row.transfer.number}'
        return super().get_source(row)


@method_decorator(never_cache, name='dispatch')
class LedgerAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, GenericAPIView):
    queryset = FinancialAccount.objects.all()
    serializer_class = LedgerRequestSerializer
    required_permissions = STATEMENT_VIEW

    def get(self, request):
        return Response({'accounts': list(self.get_queryset().values('id', 'name'))})

    @transaction.atomic
    def post(self, request):
        filters = self.get_serializer(data=request.data)
        filters.is_valid(raise_exception=True)
        params = filters.validated_data
        account = get_object_or_404(self.get_queryset(), pk=params['account'])
        entries = account.transactions.all()
        inflow = AccountTransaction.Direction.INFLOW
        opening = account.opening_balance
        prior = entries.filter(date__lt=params['start_date']).order_by().values_list('direction', 'amount')
        for direction, amount in prior.iterator():
            opening += amount if direction == inflow else -amount

        rows = list(entries.filter(date__range=(params['start_date'], params['end_date']))
                    .select_related('transfer', 'expense_payment', 'income_payment', 'sales_payment', 'purchase_payment')
                    .order_by('date', 'pk'))
        total_in = total_out = Decimal('0.0000')
        for row in rows:
            if row.direction == inflow:
                total_in += row.amount
            else:
                total_out += row.amount
            row.running_balance = opening + total_in - total_out

        return Response({
            'account': {'id': account.pk, 'name': account.name, 'currency': account.currency},
            'start_date': params['start_date'], 'end_date': params['end_date'],
            'opening_balance': format(opening, '.4f'), 'total_inflow': format(total_in, '.4f'),
            'total_outflow': format(total_out, '.4f'), 'closing_balance': format(opening + total_in - total_out, '.4f'),
            'results': LedgerEntrySerializer(rows, many=True).data,
        })


class CashFlowTransactionSerializer(AccountTransactionSerializer):
    account_name = serializers.CharField(source='account.name', read_only=True)

    class Meta(AccountTransactionSerializer.Meta):
        fields = [*AccountTransactionSerializer.Meta.fields, 'account_name']
        read_only_fields = fields


class CashFlowFilterSerializer(serializers.Serializer):
    account = serializers.IntegerField(required=False, min_value=1, max_value=2**63 - 1)


class CashFlowListAPIView(AccountTransactionListAPIView):
    serializer_class = CashFlowTransactionSerializer
    search_fields = [*AccountTransactionListAPIView.search_fields, 'account__name']

    def get_queryset(self):
        queryset = super().get_queryset()
        filters = CashFlowFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        account = filters.validated_data.get('account')
        return queryset.filter(account_id=account) if account is not None else queryset


class PostingSerializer(serializers.Serializer):
    date = serializers.DateField(default=timezone.localdate)
    amount = serializers.DecimalField(max_digits=15, decimal_places=4, min_value=MIN_AMOUNT)
    reference = serializers.CharField(max_length=150, allow_blank=True, default='')
    request_key = serializers.UUIDField()


class TransferPostingSerializer(PostingSerializer):
    transfer_type = serializers.ChoiceField(choices=TransferType.choices, default=TransferType.FUND_TRANSFER)
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
                  'destination_account_name', 'currency', 'date', 'amount', 'reference', 'transfer_type']
        read_only_fields = fields


class TransferListFilterSerializer(serializers.Serializer):
    transfer_type = serializers.CharField(required=False)

    def validate_transfer_type(self, value):
        try:
            return TransferType(value.lower().replace(' ', '_'))
        except ValueError:
            raise serializers.ValidationError('Choose fund_transfer or money_transfer.') from None


class AccountTransferListCreateAPIView(WarehouseScopeAPIMixin, BaseListCreateAPIView):
    queryset = AccountTransfer.objects.select_related('source_account', 'destination_account')
    serializer_class = AccountTransferSerializer
    warehouse_lookup = 'source_account__warehouse_id'
    required_permissions = {'GET': TRANSFER_VIEW, 'POST': (*TRANSFER_VIEW, 'accounting.add_accounttransfer')}
    search_fields = ['reference', 'source_account__name', 'destination_account__name']
    ordering = ['-pk']

    def get_queryset(self):
        # Both accounts must be visible before disclosing the transfer details.
        queryset = scoped_queryset(super().get_queryset(), self.request.warehouse_scope, 'destination_account__warehouse_id')
        params = self.request.query_params
        filters = TransferListFilterSerializer(data=params if 'transfer_type' in params else self.request.data)
        filters.is_valid(raise_exception=True)
        self.transfer_type = filters.validated_data.get('transfer_type')
        return queryset.filter(transfer_type=self.transfer_type) if self.transfer_type else queryset

    def get_paginated_response(self, data):
        response = super().get_paginated_response(data)
        # A client following a pagination link need not resend the original GET body.
        if self.transfer_type:
            for key in ('next', 'previous'):
                if response.data[key]:
                    response.data[key] = replace_query_param(response.data[key], 'transfer_type', self.transfer_type)
        return response

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

class IncomePaymentSerializer(PaymentSerializer):
    class Meta:
        model = IncomePayment
        fields = [*AUDIT_FIELDS, 'name', 'income', *PaymentSerializer.payment_fields]
        read_only_fields = fields


class IncomePaymentListAPIView(WarehouseScopeAPIMixin, BaseListAPIView):
    queryset = IncomePayment.objects.select_related('account')
    serializer_class = IncomePaymentSerializer
    warehouse_lookup = 'account__warehouse_id'
    required_permissions = INCOME_PAYMENT_VIEW
    search_fields = ['reference', 'account__name']
    ordering = ['-pk']

    def get_queryset(self):
        income = get_object_or_404(scoped_queryset(Income.objects.all(), self.request.warehouse_scope),
                                   pk=self.kwargs['income_pk'])
        return super().get_queryset().filter(income=income)


class TenderInputSerializer(serializers.Serializer):
    method = serializers.ChoiceField(choices=PaymentMethod.choices)
    account = serializers.IntegerField(min_value=1, max_value=2**63 - 1, required=False, allow_null=True)
    account_id = serializers.IntegerField(min_value=1, max_value=2**63 - 1, required=False, allow_null=True)
    amount = serializers.DecimalField(max_digits=15, decimal_places=4)
    cheque_number = serializers.CharField(max_length=60, required=False, allow_blank=True, default='')
    card_last4 = serializers.CharField(max_length=4, required=False, allow_blank=True, default='')
    gift_card_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, default='')


class OrderPaymentCreateSerializer(serializers.Serializer):
    request_key = serializers.UUIDField()
    payment_date = serializers.DateField(default=timezone.localdate)
    tenders = TenderInputSerializer(many=True, allow_empty=False)
    route_excess_to_deposit = serializers.BooleanField(required=False, default=False)


class SalesPaymentSerializer(PaymentSerializer):
    type_label = serializers.CharField()
    account_name = serializers.CharField(source='account.name', default=None)
    currency = serializers.CharField(source='sales_order.currency_id')

    class Meta:
        model = SalesPayment
        fields = [*AUDIT_FIELDS, 'name', 'sales_order', 'sales_return', 'type_label', 'cheque_number',
                  'card_last4', 'gift_card', 'note', 'is_reversal', 'reversed_payment', 'refund_of',
                  'origin', 'settlement', *PaymentSerializer.payment_fields]
        read_only_fields = fields


class SalesPaymentListCreateAPIView(WarehouseScopeAPIMixin, BaseListCreateAPIView):
    queryset = SalesPayment.objects.select_related('account', 'sales_order')
    serializer_class = SalesPaymentSerializer
    warehouse_lookup = 'sales_order__warehouse_id'
    required_permissions = {
        'GET': SALES_PAYMENT_VIEW,
        'POST': ('sale.view_salesorder', 'accounting.add_sales_payment'),
    }
    search_fields = ['reference', 'account__name']
    ordering = ['-pk']

    def get_queryset(self):
        order = get_object_or_404(completed_orders(self.request.warehouse_scope), pk=self.kwargs['sales_order_pk'])
        return super().get_queryset().filter(sales_order=order)

    def create(self, request, *args, **kwargs):
        serializer = OrderPaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = post_sales_payment(
            request.user,
            self.kwargs['sales_order_pk'],
            data['tenders'],
            data['payment_date'],
            data['request_key'],
            route_excess_to_deposit=data['route_excess_to_deposit'],
        )
        return Response(result, status=status.HTTP_200_OK)


class PurchasePaymentSerializer(PaymentSerializer):
    type_label = serializers.CharField()
    account_name = serializers.CharField(source='account.name', default=None)
    currency = serializers.CharField(source='purchase_order.currency_id')

    class Meta:
        model = PurchasePayment
        fields = [*AUDIT_FIELDS, 'name', 'purchase_order', 'purchase_return', 'type_label', 'cheque_number',
                  'card_last4', 'note', 'is_reversal', 'reversed_payment', 'refund_of',
                  'origin', 'settlement', *PaymentSerializer.payment_fields]
        read_only_fields = fields


class PurchasePaymentListCreateAPIView(WarehouseScopeAPIMixin, BaseListCreateAPIView):
    queryset = PurchasePayment.objects.select_related('account', 'purchase_order')
    serializer_class = PurchasePaymentSerializer
    warehouse_lookup = 'purchase_order__warehouse_id'
    required_permissions = {
        'GET': PURCHASE_PAYMENT_VIEW,
        'POST': ('purchase.view_purchaseorder', 'accounting.add_purchase_payment'),
    }
    search_fields = ['reference', 'account__name']
    ordering = ['-pk']

    def get_queryset(self):
        order = get_object_or_404(received_orders(self.request.warehouse_scope), pk=self.kwargs['purchase_order_pk'])
        return super().get_queryset().filter(purchase_order=order)

    def create(self, request, *args, **kwargs):
        serializer = OrderPaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = post_purchase_payment(
            request.user,
            self.kwargs['purchase_order_pk'],
            data['tenders'],
            data['payment_date'],
            data['request_key'],
        )
        return Response(result, status=status.HTTP_200_OK)


class PaymentReverseSerializer(serializers.Serializer):
    request_key = serializers.UUIDField()
    reason = serializers.CharField(max_length=255, allow_blank=False)
    kind = serializers.ChoiceField(choices=[('sales', 'Sales'), ('purchase', 'Purchase')], required=False)


@method_decorator(never_cache, name='dispatch')
class PaymentReverseAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, GenericAPIView):
    serializer_class = PaymentReverseSerializer
    required_permissions = ('accounting.reverse_payment',)
    http_method_names = ['post', 'options']
    default_kind = None

    def post(self, request, pk):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = reverse_payment(
            request.user, pk, data['request_key'], data['reason'],
            kind=data.get('kind') or self.default_kind,
        )
        return Response(result, status=status.HTTP_200_OK)


class PartyDueSummaryFilterSerializer(serializers.Serializer):
    as_of = serializers.DateField(required=False)
    warehouse_id = serializers.IntegerField(min_value=1, required=False)


@method_decorator(never_cache, name='dispatch')
class PartyDueSummaryAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = ('people.view_party',)
    http_method_names = ['get', 'head', 'options']

    def get(self, request, pk):
        flt = PartyDueSummaryFilterSerializer(data=request.query_params)
        flt.is_valid(raise_exception=True)
        data = flt.validated_data
        return Response(party_due_summary(
            pk, as_of=data.get('as_of'), scope=request.warehouse_scope, warehouse_id=data.get('warehouse_id'),
        ))


class ClearDuePreviewSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=15, decimal_places=4, min_value=MIN_AMOUNT)
    direction = serializers.ChoiceField(choices=[('receipt', 'Receipt'), ('disbursement', 'Disbursement')])


class ClearDueConfirmSerializer(ClearDuePreviewSerializer):
    account = serializers.IntegerField(min_value=1, max_value=2**63 - 1)
    method = serializers.ChoiceField(choices=PaymentMethod.choices)
    payment_date = serializers.DateField(default=timezone.localdate)
    request_key = serializers.UUIDField()
    note = serializers.CharField(required=False, allow_blank=True, default='')


@method_decorator(never_cache, name='dispatch')
class ClearDuePreviewAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, GenericAPIView):
    serializer_class = ClearDuePreviewSerializer
    required_permissions = ('accounting.clear_due',)
    http_method_names = ['post', 'options']

    def post(self, request, pk):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        return Response(preview_clear_due(pk, data['amount'], data['direction'], scope=request.warehouse_scope))


@method_decorator(never_cache, name='dispatch')
class ClearDueConfirmAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, GenericAPIView):
    serializer_class = ClearDueConfirmSerializer
    required_permissions = ('accounting.clear_due',)
    http_method_names = ['post', 'options']

    def post(self, request, pk):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = confirm_clear_due(
            request.user, pk, data['amount'], data['direction'], data['account'],
            data['method'], data['payment_date'], data['request_key'], note=data['note'],
        )
        return Response(result, status=status.HTTP_200_OK)



class DepositEntrySerializer(serializers.ModelSerializer):
    class Meta:
        from .models import DepositEntry
        model = DepositEntry
        fields = ['id', 'customer', 'kind', 'amount', 'account', 'source', 'sales_payment', 'note', 'request_key', 'created_at']
        read_only_fields = fields


class CustomerDepositListCreateAPIView(BaseAPIMixin, APIView):
    required_permissions = {
        'GET': ('people.view_party',),
        'POST': ('accounting.add_deposit',),
    }
    http_method_names = ['get', 'post', 'head', 'options']

    def get(self, request, customer_id):
        from .models import DepositEntry
        from people.models import Party
        cust = get_object_or_404(Party, pk=customer_id, is_customer=True)
        entries = DepositEntry.objects.filter(customer=cust).order_by('-pk')
        return Response({
            'customer_id': cust.pk,
            'deposit_balance': str(cust.deposit_balance),
            'results': DepositEntrySerializer(entries, many=True).data,
        })

    def post(self, request, customer_id):
        from uuid import UUID
        from sale.loyalty import add_deposit
        req_key = request.data.get('request_key')
        if not req_key:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'request_key': 'request_key UUID is required.'})
        acct_id = request.data.get('account')
        if not acct_id:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'account': 'account is required.'})
        entry, created = add_deposit(
            request.user, customer_id, request.data.get('amount') or 0,
            int(acct_id), request.data.get('note') or '', UUID(str(req_key)),
        )
        return Response(
            {**DepositEntrySerializer(entry).data, 'idempotent_replay': not created},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
