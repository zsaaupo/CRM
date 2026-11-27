from decimal import Decimal
from uuid import uuid4

from django.shortcuts import get_object_or_404, redirect
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from account.page_views import ProtectedAccountPageView
from account.permissions import has_role_permissions
from common.view import BaseAPIMixin, BasePagination
from warehouse.scope import WarehouseScopeAPIMixin, has_warehouse_scope, scoped_queryset, warehouse_scope
from .models import PurchaseOrder, PurchaseReceipt, PurchaseReceiptLine
from .receipts import post_receipt_action
from .services import PurchaseBusy


class ReceiptLineInput(serializers.Serializer):
    line = serializers.IntegerField(min_value=1, max_value=2**63-1)
    quantity = serializers.DecimalField(max_digits=18, decimal_places=6, min_value=Decimal('0.000001'))


class ReceiptInput(serializers.Serializer):
    request_key = serializers.UUIDField()
    lines = ReceiptLineInput(many=True, allow_empty=False)

    def validate_lines(self, rows):
        if len({row['line'] for row in rows}) != len(rows):
            raise ValidationError('Choose each order line once.')
        return rows


class InvoiceInput(serializers.Serializer):
    receipt = serializers.IntegerField(min_value=1, max_value=2**63-1)
    invoice_reference = serializers.CharField(max_length=100)


class ReceiptLineOutput(serializers.ModelSerializer):
    class Meta:
        model = PurchaseReceiptLine
        fields = ('order_item', 'quantity')


class ReceiptOutput(serializers.ModelSerializer):
    lines = ReceiptLineOutput(many=True)

    class Meta:
        model = PurchaseReceipt
        fields = ('id', 'order', 'request_key', 'created_at', 'create_by', 'amount_base', 'final_delivery',
                  'invoice_reference', 'invoiced_at', 'invoiced_by', 'lines')


class ReceiptResource:
    invoice = False

    @property
    def required_permissions(self):
        required = ('purchase.view_purchaseorder', 'purchase.view_purchaseorderitem')
        if self.request.method == 'POST':
            return (*required, 'purchase.match_purchaseinvoice' if self.invoice else 'purchase.change_purchaseorder')
        return required

    def order(self):
        return get_object_or_404(scoped_queryset(PurchaseOrder.objects.all(), warehouse_scope(self.request.user)), pk=self.kwargs['pk'])

    def submit(self, data):
        form = (InvoiceInput if self.invoice else ReceiptInput)(data=data)
        form.is_valid(raise_exception=True)
        return post_receipt_action(self.request.user, self.kwargs['pk'], form.validated_data, invoice=self.invoice)


class ReceiptAPI(ReceiptResource, WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    def get(self, request, pk):
        page = BasePagination()
        rows = page.paginate_queryset(self.order().receipts.prefetch_related('lines'), request)
        return page.get_paginated_response(ReceiptOutput(rows, many=True).data)

    def post(self, request, pk):
        receipt, changed = self.submit(request.data)
        return Response(ReceiptOutput(receipt).data, status=201 if changed else 200)


class ReceiptPage(ReceiptResource, ProtectedAccountPageView):
    template_name = 'purchase/receipts.html'

    def has_permission(self):
        return has_role_permissions(self.request.user, self.required_permissions) and has_warehouse_scope(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        order = self.order()
        items = list(order.items.select_related('product', 'unit'))
        for line in items:
            line.delivered = line.quantity if order.is_received and not order.receipts_started else line.received_quantity
            line.outstanding = line.quantity - line.delivered
        context.update(order=order, items=items, receipts=order.receipts.all(), request_key=uuid4(),
            can_receive=has_role_permissions(self.request.user, ('purchase.change_purchaseorder',)),
            can_invoice=has_role_permissions(self.request.user, ('purchase.match_purchaseinvoice',)))
        return context

    def post(self, request, pk):
        data = request.POST if self.invoice else dict(request_key=request.POST.get('request_key'),
            lines=[{'line': k[4:], 'quantity': v} for k, v in request.POST.items() if k.startswith('qty_') and v])
        try:
            self.submit(data)
        except (ValidationError, PurchaseBusy) as error:
            return self.render_to_response(self.get_context_data(errors=error.detail), status=error.status_code)
        return redirect('purchase_pages:receipts', pk=pk)
