from uuid import uuid4

from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from account.page_views import ProtectedAccountPageView
from account.permissions import has_role_permissions
from common.view import BaseAPIMixin
from sale.models import SalesOrder
from warehouse.scope import WarehouseScopeAPIMixin, has_warehouse_scope, scoped_queryset, warehouse_scope

from .models import MIN_AMOUNT, TenderMethod, base_currency
from .services import PURCHASE_PAYMENT_VIEW, SALES_PAYMENT_VIEW, available_payment_accounts
from .settlement import balance, settle_order


class TenderSerializer(serializers.Serializer):
    account = serializers.IntegerField(min_value=1, max_value=2**63 - 1)
    method = serializers.ChoiceField(choices=TenderMethod.choices)
    amount = serializers.DecimalField(max_digits=15, decimal_places=4, min_value=MIN_AMOUNT)


class SettlementInput(serializers.Serializer):
    request_key = serializers.UUIDField()
    date = serializers.DateField()
    reference = serializers.CharField(required=False, allow_blank=True, max_length=150, default='')
    tenders = TenderSerializer(many=True, allow_empty=False, max_length=100)


class OrderSettlementResource:
    order_model = SalesOrder

    @property
    def read_permissions(self):
        return SALES_PAYMENT_VIEW if self.order_model is SalesOrder else PURCHASE_PAYMENT_VIEW

    @property
    def write_permission(self):
        return 'accounting.add_salespayment' if self.order_model is SalesOrder else 'accounting.add_purchasepayment'

    def order(self):
        scope = warehouse_scope(self.request.user)
        return get_object_or_404(scoped_queryset(self.order_model.objects.all(), scope), pk=self.kwargs['pk'])

    def submit(self, data):
        serializer = SettlementInput(data=data)
        serializer.is_valid(raise_exception=True)
        return settle_order(self.request.user, self.order_model, self.kwargs['pk'], **serializer.validated_data)


class OrderSettlementAPI(OrderSettlementResource, WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    @property
    def required_permissions(self):
        return (*self.read_permissions, self.write_permission) if self.request.method == 'POST' else self.read_permissions

    def get(self, request, pk):
        return Response({'currency': base_currency(), **display_balance(self.order())})

    def post(self, request, pk):
        command, amounts, created = self.submit(request.data)
        return Response({'command': command.pk, 'request_key': command.request_key, 'currency': base_currency(),
                         **format_amounts(amounts)}, status=201 if created else 200)


def format_amounts(amounts):
    return {k: v if k == 'payment_status' else format(v, '.4f') for k, v in amounts.items()}


def display_balance(order):
    return format_amounts(balance(order))


class OrderSettlementPage(OrderSettlementResource, ProtectedAccountPageView):
    template_name = 'accounting/order_settlement.html'

    def has_permission(self):
        needed = self.read_permissions
        if self.request.method == 'POST':
            needed = (*needed, self.write_permission)
        return has_role_permissions(self.request.user, needed) and has_warehouse_scope(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        order = self.order()
        accounts = available_payment_accounts(warehouse_scope(self.request.user))
        context.update(order=order, amounts=display_balance(order), currency=base_currency(),
                       accounts=accounts.filter(Q(warehouse_id=order.warehouse_id) | Q(warehouse__isnull=True)),
                       methods=TenderMethod.choices, today=timezone.localdate(), request_key=uuid4(),
                       payments=order.payments.select_related('account'),
                       can_post=has_role_permissions(self.request.user, (self.write_permission,)))
        return context

    def post(self, request, **kwargs):
        data = request.POST
        try:
            accounts, methods, amounts = (data.getlist(key) for key in ('account', 'method', 'amount'))
            if len(accounts) != len(methods) or len(accounts) != len(amounts):
                raise ValidationError({'tenders': 'Enter an account, method and amount for each tender.'})
            self.submit({'request_key': data.get('request_key'), 'date': data.get('date'),
                         'reference': data.get('reference', ''), 'tenders': [
                             dict(account=account, method=method, amount=amount)
                             for account, method, amount in zip(accounts, methods, amounts, strict=True)]})
        except ValidationError as error:
            messages = serializers.as_serializer_error(error)
            return self.render_to_response(self.get_context_data(errors=messages), status=400)
        return redirect(request.path)
