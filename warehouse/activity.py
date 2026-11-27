from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers

from accounting.api_views import AccountTransferListCreateAPIView, ExpenseListCreateAPIView, IncomeListCreateAPIView
from accounting.services import TRANSFER_VIEW
from purchase.api_views import PurchaseOrderListAPIView
from purchase.return_api import PurchaseReturnListAPIView
from sale.api_views import QuotationListCreateAPIView, SalesOrderListAPIView
from sale.return_api import SalesReturnListAPIView

from .api_views import WarehouseTransferListAPIView
from .models import WareHouse
from .scope import scoped_queryset


class ReportDateRange(serializers.Serializer):
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)

    def validate(self, dates):
        if 'start_date' in dates and 'end_date' in dates and dates['end_date'] < dates['start_date']:
            raise serializers.ValidationError({'end_date': 'Choose an end date on or after the start date.'})
        return dates


class WarehouseActivity:
    """Narrow an existing permission-checked list to the selected warehouse."""
    http_method_names = ['get', 'head', 'options']
    warehouse_fields = ('warehouse_id',)
    activity_date = 'date'

    def get_queryset(self):
        warehouse = get_object_or_404(scoped_queryset(WareHouse.objects.only('pk'), self.request.warehouse_scope, 'pk'),
                                      pk=self.kwargs['warehouse_id'])
        match = Q()
        for field in self.warehouse_fields:
            match |= Q(**{field: warehouse.pk})
        period = ReportDateRange(data=self.request.query_params)
        period.is_valid(raise_exception=True)
        dates = period.validated_data
        if 'start_date' in dates:
            match &= Q(**{f'{self.activity_date}__gte': dates['start_date']})
        if 'end_date' in dates:
            match &= Q(**{f'{self.activity_date}__lte': dates['end_date']})
        return super().get_queryset().filter(match)


class SalesReport(WarehouseActivity, SalesOrderListAPIView):
    activity_date = 'order_date'


class PurchaseReport(WarehouseActivity, PurchaseOrderListAPIView):
    activity_date = 'order_date'


class SalesReturnReport(WarehouseActivity, SalesReturnListAPIView):
    activity_date = 'return_date'
    warehouse_fields = ('sales_order__warehouse_id',)


class PurchaseReturnReport(WarehouseActivity, PurchaseReturnListAPIView):
    activity_date = 'return_date'
    warehouse_fields = ('purchase_order__warehouse_id',)


class ExpenseReport(WarehouseActivity, ExpenseListCreateAPIView):
    pass


class IncomeReport(WarehouseActivity, IncomeListCreateAPIView):
    pass


class QuotationReport(WarehouseActivity, QuotationListCreateAPIView):
    activity_date = 'order_date'


class StockTransferReport(WarehouseActivity, WarehouseTransferListAPIView):
    activity_date = 'transfer_date'
    warehouse_fields = ('source_warehouse_id', 'destination_warehouse_id')


class FinancialTransferReport(WarehouseActivity, AccountTransferListCreateAPIView):
    warehouse_fields = ('source_account__warehouse_id', 'destination_account__warehouse_id')
    kind = None

    def get_queryset(self):
        return super().get_queryset().filter(transfer_type=self.kind)


class FundTransferReport(FinancialTransferReport):
    kind = 'fund_transfer'


class MoneyTransferReport(FinancialTransferReport):
    kind = 'money_transfer'


finance_columns = [('id', 'Record'), ('date', 'Date'), ('category_name', 'Category'),
                  ('payment_account_name', 'Account'), ('amount', 'Amount', 'price'), ('is_completed', 'Completed', 'boolean')]
transfer_columns = [('transfer_number', 'Transfer'), ('date', 'Date'), ('source_account_name', 'From'),
                    ('destination_account_name', 'To'), ('amount', 'Amount', 'price'), ('reference', 'Reference')]
REPORTS = {
    'sales': ('Sales', SalesReport, [('order_number', 'Order'), ('order_date', 'Date'), ('customer_name', 'Customer'),
                                     ('grand_total', 'Total', 'price'), ('is_completed', 'Completed', 'boolean')]),
    'purchases': ('Purchases', PurchaseReport, [('order_number', 'Order'), ('order_date', 'Date'), ('supplier_name', 'Supplier'),
                                               ('grand_total', 'Total', 'price'), ('is_received', 'Received', 'boolean')]),
    'sales-returns': ('Sales returns', SalesReturnReport, [('return_number', 'Return'), ('return_date', 'Date'),
                       ('sales_order_number', 'Sale'), ('customer_name', 'Customer'), ('is_completed', 'Completed', 'boolean')]),
    'purchase-returns': ('Purchase returns', PurchaseReturnReport, [('return_number', 'Return'), ('return_date', 'Date'),
                          ('purchase_order_number', 'Purchase'), ('supplier_name', 'Supplier'), ('is_completed', 'Completed', 'boolean')]),
    'expenses': ('Expenses', ExpenseReport, finance_columns),
    'income': ('Income', IncomeReport, finance_columns),
    'quotations': ('Quotations', QuotationReport, [('quotation_number', 'Quotation'), ('order_date', 'Date'),
                   ('customer_name', 'Customer'), ('grand_total', 'Total', 'price'), ('status_name', 'Status')]),
    'stock-transfers': ('Warehouse transfers', StockTransferReport, [('transfer_number', 'Transfer'), ('transfer_date', 'Date'),
                         ('source_warehouse_name', 'From'), ('destination_warehouse_name', 'To'), ('stage_label', 'Stage')]),
    'fund-transfers': ('Fund transfers', FundTransferReport, transfer_columns),
    'money-transfers': ('Money transfers', MoneyTransferReport, transfer_columns),
}


def available_reports(scope):
    if scope is None:
        return REPORTS
    granted = {f'{app}.{code}' for app, code in scope.role.permissions.values_list('content_type__app_label', 'codename')}
    available = {}
    for key, report in REPORTS.items():
        view = report[1]
        model = view.queryset.model._meta
        permissions = TRANSFER_VIEW if issubclass(view, FinancialTransferReport) else (f'{model.app_label}.view_{model.model_name}',)
        if granted.issuperset(permissions):
            available[key] = report
    return available
