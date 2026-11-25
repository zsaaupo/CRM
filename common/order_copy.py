from django.db import transaction
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from account.permissions import has_role_permissions
from accounting.services import lock_rows
from common.view import BaseAPIMixin
from purchase.models import PurchaseOrder
from purchase.serializers import PurchaseOrderCreateSerializer
from sale.models import SalesOrder
from sale.serializers import SalesOrderCreateSerializer
from warehouse.scope import WarehouseScopeAPIMixin, lock_warehouse_scope, scoped_queryset


def copy_permissions(model):
    label = model._meta.label_lower
    app, order = label.split('.')
    return tuple(f'{app}.{action}_{entity}' for action in ('view', 'add') for entity in (order, order + 'item'))


class DuplicateOrderView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    model = SalesOrder

    @property
    def required_permissions(self):
        return copy_permissions(self.model)

    @transaction.atomic
    def post(self, request, pk):
        scope = lock_warehouse_scope(request.user)
        if not has_role_permissions(request.user, self.required_permissions):
            raise PermissionDenied()
        source = get_object_or_404(lock_rows(scoped_queryset(self.model.objects.filter(pk=pk), scope)))
        sale = self.model is SalesOrder
        party = 'customer' if sale else 'supplier'
        data = {party: getattr(source, f'{party}_id'), 'currency': source.currency_id,
                'warehouse': source.warehouse_id, 'payment_account': source.payment_account_id,
                'settlement_mode': source.settlement_mode, 'order_date': timezone.localdate(),
                'exchange_rate': source.exchange_rate, 'shipping_cost': source.shipping_cost, 'notes': source.notes}
        if sale:
            data.update(billing_address=source.billing_address, shipping_address=source.shipping_address)
        date_field = 'expected_delivery_date' if sale else 'expected_date'
        expected = getattr(source, date_field)
        if expected and expected >= data['order_date']:
            data[date_field] = expected
        lines = list(source.items.order_by('line_number').values(
            'line_number', 'product', 'unit', 'quantity', 'description', 'unit_price', 'discount_amount', 'tax_rate'))
        if lines:
            data['items'] = lines
        serializer = (SalesOrderCreateSerializer if sale else PurchaseOrderCreateSerializer)(
            data=data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        actor = request.user.get_username()
        draft = serializer.save(create_by=actor, modified_by=actor)
        route = 'sale_pages:sale-detail' if sale else 'purchase_pages:purchase-detail'
        return Response({'id': draft.pk, 'order_number': draft.order_number, 'url': reverse(route, args=[draft.pk])},
                        status=201)
