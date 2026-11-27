from uuid import uuid4, uuid5

from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from account.page_views import ProtectedAccountPageView
from account.permissions import has_role_permissions
from common.view import BaseAPIMixin
from people.models import Party
from purchase.models import PurchaseOrder
from sale.models import SalesOrder
from warehouse.scope import WarehouseScopeAPIMixin, has_warehouse_scope, scoped_queryset, warehouse_scope

from .models import MIN_AMOUNT, SettlementCommand, TenderMethod, ZERO, base_currency
from .services import PURCHASE_PAYMENT_VIEW, SALES_PAYMENT_VIEW, available_payment_accounts, create_source, lock_financial_scope, lock_rows
from .settlement import balance, settle_order, with_settlement_history
from .settlement_views import format_amounts


class Allocation(serializers.Serializer):
    order = serializers.IntegerField(min_value=1, max_value=2**63 - 1)
    amount = serializers.DecimalField(max_digits=15, decimal_places=4, min_value=MIN_AMOUNT)


class PartyPayment(serializers.Serializer):
    request_key = serializers.UUIDField()
    account = serializers.IntegerField(min_value=1, max_value=2**63 - 1)
    method = serializers.ChoiceField(choices=TenderMethod.choices)
    reference = serializers.CharField(max_length=150, required=False, default='', allow_blank=True)
    date = serializers.DateField()
    allocations = Allocation(many=True, allow_empty=False, max_length=100)

    def validate_allocations(self, lines):
        if len({row['order'] for row in lines}) != len(lines):
            raise ValidationError('Choose each order once.')
        return sorted(lines, key=lambda row: row['order'])


class StatementResource:
    party_role = 'customer'

    @property
    def order_model(self):
        return SalesOrder if self.party_role == 'customer' else PurchaseOrder

    @property
    def required_permissions(self):
        permissions = SALES_PAYMENT_VIEW if self.party_role == 'customer' else PURCHASE_PAYMENT_VIEW
        needed = (*permissions, 'people.view_party')
        if self.request.method == 'POST':
            needed += ('accounting.add_salespayment' if self.party_role == 'customer' else 'accounting.add_purchasepayment',)
        return needed

    def party(self):
        return get_object_or_404(Party.objects.filter(**{f'is_{self.party_role}': True}), pk=self.kwargs['pk'])

    def orders(self, party, scope):
        posted = Q(is_completed=True) if self.party_role == 'customer' else Q(is_received=True) | Q(receipts_started=True)
        return scoped_queryset(self.order_model.objects.filter(posted, **{self.party_role: party}), scope)

    def statement(self):
        party = self.party()
        orders = self.orders(party, warehouse_scope(self.request.user))
        totals = dict.fromkeys(('total', 'paid', 'refunded', 'returned', 'due'), ZERO)
        page = Paginator(orders.order_by('order_date', 'pk'), 50).get_page(self.request.GET.get('page'))
        first = page.start_index()
        last = page.end_index()
        rows = []
        route = 'sale-settlement' if self.party_role == 'customer' else 'purchase-settlement'
        history = with_settlement_history(orders.order_by('order_date', 'pk'))
        for index, order in enumerate(history.iterator(chunk_size=250), start=1):
            amounts = balance(order)
            for field in totals:
                totals[field] += amounts[field]
            if first <= index <= last:
                rows.append(dict(order=order.pk, number=order.order_number, date=order.order_date, warehouse=order.warehouse_id,
                                 settlement_url=reverse(f'accounting_pages:{route}', args=[order.pk]), **format_amounts(amounts)))
        return dict(party=party.name, currency=base_currency(), rows=rows, totals=format_amounts(totals),
                    page=page.number, pages=page.paginator.num_pages, count=page.paginator.count)

    @transaction.atomic
    def allocate(self, data):
        form = PartyPayment(data=data)
        form.is_valid(raise_exception=True)
        values = form.validated_data
        actor = self.request.user
        scope = lock_financial_scope(actor, *self.required_permissions)
        party = self.party()
        allocations = values['allocations']
        ids = [row['order'] for row in allocations]
        allowed = list(lock_rows(self.orders(party, scope).filter(pk__in=ids)).values_list('pk', flat=True))
        if allowed != ids:
            raise ValidationError({'allocations': 'Choose posted orders from this party within your warehouse access.'})
        details = dict(party=party.pk, role=self.party_role, account=values['account'], method=values['method'],
                       date=values['date'].isoformat(), reference=values['reference'],
                       allocations=[dict(order=row['order'], amount=str(row['amount'])) for row in allocations])
        batch, created = create_source(SettlementCommand, actor, values['request_key'], details=details)
        if created:
            for row in allocations:
                settle_order(actor, self.order_model, row['order'], request_key=uuid5(batch.request_key, str(row['order'])),
                             date=values['date'], reference=values['reference'],
                             tenders=[dict(account=values['account'], method=values['method'], amount=row['amount'])])
        return batch.pk, created


class PartyStatementAPI(StatementResource, WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    def get(self, request, pk):
        return Response(self.statement())

    def post(self, request, pk):
        batch, created = self.allocate(request.data)
        return Response({'command': batch}, status=201 if created else 200)


class PartyStatementPage(StatementResource, ProtectedAccountPageView):
    template_name = 'accounting/party_statement.html'

    def has_permission(self):
        return has_role_permissions(self.request.user, self.required_permissions) and has_warehouse_scope(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        summary = self.statement()
        accounts = available_payment_accounts(warehouse_scope(self.request.user))
        warehouses = {row['warehouse'] for row in summary['rows']}
        permission = 'accounting.add_salespayment' if self.party_role == 'customer' else 'accounting.add_purchasepayment'
        context.update(**summary, methods=TenderMethod.choices, request_key=uuid4(), today=timezone.localdate(),
                       can_post=has_role_permissions(self.request.user, (permission,)),
                       accounts=accounts.filter(Q(warehouse_id__in=warehouses) | Q(warehouse__isnull=True)))
        return context

    def post(self, request, pk):
        data = request.POST.dict()
        data['allocations'] = [dict(order=key[6:], amount=value) for key, value in request.POST.items()
                               if key.startswith('order_') and value.strip()]
        try:
            self.allocate(data)
        except ValidationError as exc:
            return self.render_to_response(self.get_context_data(errors=exc.detail), status=400)
        return HttpResponseRedirect(request.get_full_path())
