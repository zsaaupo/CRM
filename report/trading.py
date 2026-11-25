import csv
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal, localcontext

from django import forms
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin

from account.permissions import has_role_permissions
from accounting.models import AccountTransaction, base_currency
from accounting.services import STATEMENT_VIEW
from purchase.models import PurchaseOrder
from sale.models import MONEY_PRECISION, SalesOrder
from warehouse.models import WareHouse
from warehouse.scope import has_warehouse_scope, scoped_queryset, warehouse_scope


TRADE = {
    'sales': (SalesOrder, 'is_completed', ('sale.view_salesorder', 'sale.view_salesorderitem'), 'sale_pages:sale-detail'),
    'purchases': (PurchaseOrder, 'is_received', ('purchase.view_purchaseorder', 'purchase.view_purchaseorderitem'),
                  'purchase_pages:purchase-detail'),
}


class Period(forms.Form):
    warehouse = forms.ModelChoiceField(WareHouse.objects.none(), required=False, empty_label='All accessible warehouses')
    start_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    end_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))

    def clean(self):
        data = super().clean()
        today = timezone.localdate()
        start = data.get('start_date') or today.replace(day=1)
        end = data.get('end_date') or today
        if start > end:
            self.add_error('end_date', 'Choose an end date on or after the start date.')
        return {**data, 'start_date': start, 'end_date': end}


def document_rows(key, scope, branch, dates):
    model, posted, _, page = TRADE[key]
    orders = scoped_queryset(model.objects.filter(**{posted: True}, order_date__range=dates), scope)
    if branch:
        orders = orders.filter(warehouse=branch)
    orders = orders.select_related('warehouse').prefetch_related('items').order_by('-order_date', '-pk')
    with localcontext() as ctx:
        ctx.prec = 50
        for order in orders.iterator(chunk_size=250):
            total = (order.grand_total * order.exchange_rate).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
            yield dict(number=order.order_number, date=order.order_date, warehouse=str(order.warehouse), total=total,
                       url=reverse(page, args=[order.pk]))


def dashboard_data(user, query, *, export=None):
    if not has_warehouse_scope(user):
        return {}
    allowed = [key for key, (_, _, permissions, _) in TRADE.items() if has_role_permissions(user, permissions)]
    can_read_cash = has_role_permissions(user, STATEMENT_VIEW)
    if not allowed and not can_read_cash:
        return {}
    scope = warehouse_scope(user)
    form = Period(query)
    form.fields['warehouse'].queryset = scoped_queryset(WareHouse.objects.all(), scope, 'pk').order_by('name')
    if not form.is_valid():
        return {'form': form}
    filters = form.cleaned_data
    dates = (filters['start_date'], filters['end_date'])
    branch = filters['warehouse']
    if export:
        if export not in allowed:
            return {}
        return {'rows': document_rows(export, scope, branch, dates)}
    reports = []
    for key in allowed:
        total, count, latest = Decimal('0'), 0, []
        for row in document_rows(key, scope, branch, dates):
            total += row['total']
            count += 1
            if count <= 5:
                latest.append(row)
        reports.append(dict(key=key, label='Completed sales' if key == 'sales' else 'Received purchases',
                            total=format(total, '.4f'), count=count, latest=latest))
    cash = None
    if can_read_cash:
        entries = scoped_queryset(AccountTransaction.objects.filter(date__range=dates, transfer__isnull=True),
                                  scope, 'account__warehouse_id')
        if branch:
            # Attribute a shared account's trade payments to the originating document warehouse.
            entries = entries.filter(Q(sales_payment__sales_order__warehouse=branch)
                | Q(purchase_payment__purchase_order__warehouse=branch) | Q(expense_payment__expense__warehouse=branch)
                | Q(income_payment__income__warehouse=branch))
        amounts = defaultdict(Decimal)
        for direction, amount in entries.values_list('direction', 'amount').iterator():
            amounts[direction] += amount
        cash = dict(inflow=format(amounts['inflow'], '.4f'), outflow=format(amounts['outflow'], '.4f'))
    return dict(form=form, reports=reports, cash=cash, currency=base_currency(), start=dates[0], end=dates[1])


class TradingReportView(LoginRequiredMixin, View):
    def get(self, request, key=None):
        data = dashboard_data(request.user, request.GET, export=key)
        if not data:
            return JsonResponse({'detail': 'Trading report access is required.'}, status=403)
        if 'form' in data and data['form'].errors:
            return JsonResponse({'errors': data['form'].errors.get_json_data()}, status=400)
        if key is None:
            data.pop('form')
            return JsonResponse(data)
        response = HttpResponse(content_type='text/csv; charset=utf-8',
                                headers={'Content-Disposition': f'attachment; filename="{key}.csv"'})
        writer = csv.writer(response)
        writer.writerow(['Order', 'Document date', 'Warehouse', f'Gross total ({base_currency()})'])
        for row in data['rows']:
            # Treat user-entered warehouse labels as text in spreadsheet programs.
            warehouse = row['warehouse']
            if warehouse.lstrip().startswith(('=', '+', '-', '@')) or warehouse.startswith(('\t', '\r', '\n')):
                warehouse = "'" + warehouse
            writer.writerow([row['number'], row['date'], warehouse, row['total']])
        return response
