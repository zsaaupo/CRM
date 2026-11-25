from decimal import Decimal

from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from account.page_views import ProtectedAccountPageView
from account.permissions import has_role_permissions
from common.models import SettlementMode
from common.view import BaseAPIMixin
from people.models import Party
from purchase.models import PurchaseOrder
from purchase.serializers import PurchaseOrderCreateSerializer
from warehouse.scope import WarehouseScopeAPIMixin, has_warehouse_scope, lock_warehouse_scope, scoped_queryset, warehouse_scope

from .models import SalesQuotation
from .services import QUOTE_DETAIL, QuotationConflict, eligible_lines, lock_quotation, require_permissions


PROCURE = (*QUOTE_DETAIL, 'sale.convert_salesquotation', 'people.view_party', 'purchase.add_purchaseorder',
           'purchase.add_purchaseorderitem', 'purchase.view_purchaseorder', 'purchase.view_purchaseorderitem')
ELIGIBLE = (SalesQuotation.Status.ISSUED, SalesQuotation.Status.ACCEPTED, SalesQuotation.Status.CONVERTED)


class CostLine(serializers.Serializer):
    line = serializers.IntegerField(min_value=1)
    unit_cost = serializers.DecimalField(max_digits=18, decimal_places=4, min_value=Decimal('0'))
    tax_rate = serializers.DecimalField(max_digits=7, decimal_places=4, min_value=Decimal('0'),
                                        max_value=Decimal('100'), default=Decimal('0.0000'))


class ProcurementInput(serializers.Serializer):
    supplier = serializers.IntegerField(min_value=1, max_value=2**63-1)
    costs = CostLine(many=True, allow_empty=False)


@transaction.atomic
def convert_purchase(request, pk, data):
    scope = lock_warehouse_scope(request.user)
    require_permissions(request.user, PROCURE)
    form = ProcurementInput(data=data)
    form.is_valid(raise_exception=True)
    values = form.validated_data
    costs = {row['line']: row for row in values['costs']}
    if len(costs) != len(values['costs']):
        raise ValidationError({'costs': 'Enter each quotation line once.'})
    record = lock_quotation(pk, scope)
    terms = {'supplier': values['supplier'], 'costs': [
        [line, str(costs[line]['unit_cost']), str(costs[line]['tax_rate'])] for line in sorted(costs)]}
    if record.purchase_order_id:
        if record.purchase_conversion != terms:
            raise QuotationConflict('A purchase was already created with different terms.')
        return get_object_or_404(scoped_queryset(PurchaseOrder.objects.all(), scope), pk=record.purchase_order_id), False
    if record.status not in ELIGIBLE:
        raise QuotationConflict('Issue the quotation before preparing a purchase.')
    supplier = get_object_or_404(Party.objects.select_for_update(), pk=values['supplier'], is_supplier=True, is_active=True)
    items = eligible_lines(record)
    if set(costs) != {item.pk for item in items}:
        raise ValidationError({'costs': 'Review a purchase cost for every quoted line.'})
    new = PurchaseOrderCreateSerializer(context={'request': request}, data={
        'supplier': supplier.pk, 'warehouse': record.warehouse_id, 'currency': record.currency_id,
        'exchange_rate': record.exchange_rate, 'settlement_mode': SettlementMode.DEFERRED,
        'items': [dict(line_number=item.line_number, product=item.product_id, unit=item.unit_id,
                       description=item.description, quantity=item.quantity, unit_price=costs[item.pk]['unit_cost'],
                       tax_rate=costs[item.pk]['tax_rate']) for item in items],
    })
    new.is_valid(raise_exception=True)
    actor = request.user.get_username()
    order = new.save(create_by=actor, modified_by=actor)
    record.purchase_order = order
    record.purchase_conversion = terms
    record.modified_by = actor
    record.save(update_fields=['purchase_order', 'purchase_conversion', 'modified_by', 'updated_at'])
    return order, True


class QuotePurchaseAPI(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = PROCURE

    def post(self, request, pk):
        order, created = convert_purchase(request, pk, request.data)
        return Response(dict(id=order.pk, order_number=order.order_number,
                             url=reverse('purchase_pages:purchase-detail', args=[order.pk])), status=201 if created else 200)


class QuotePurchasePage(ProtectedAccountPageView):
    template_name = 'sale/quote_purchase.html'

    def has_permission(self):
        return has_role_permissions(self.request.user, PROCURE) and has_warehouse_scope(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        record = get_object_or_404(scoped_queryset(SalesQuotation.objects.all(), warehouse_scope(self.request.user)),
                                   pk=self.kwargs['pk'], status__in=ELIGIBLE)
        context.update(quote=record, lines=record.items.select_related('product', 'unit'),
                       suppliers=Party.objects.filter(is_supplier=True, is_active=True))
        return context

    def post(self, request, pk):
        data = {'supplier': request.POST.get('supplier'), 'costs': [
            dict(line=k[5:], unit_cost=v, tax_rate=request.POST.get('tax_' + k[5:], '0'))
            for k, v in request.POST.items() if k.startswith('cost_')]}
        try:
            order, _ = convert_purchase(request, pk, data)
        except (QuotationConflict, ValidationError) as exc:
            return self.render_to_response(self.get_context_data(errors=exc.detail), status=exc.status_code)
        return redirect('purchase_pages:purchase-detail', pk=order.pk)
