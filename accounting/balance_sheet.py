"""All-time cash and fund positions for active financial accounts."""
import csv

from django import forms
from django.conf import settings
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache

from report.rendering import ReportUnavailable, render_pdf
from warehouse.scope import all_warehouse_access, scoped_queryset, warehouse_scope
from .models import ZERO, AccountTransaction, FinancialAccount
from .page_views import FinancePageView
from .services import STATEMENT_VIEW


COLUMNS = {
    'name': 'Account', 'type': 'Type', 'opening_balance': 'Opening balance',
    'inflow': 'Money in', 'outflow': 'Money out', 'balance': 'Current balance',
}
AMOUNTS = ('opening_balance', 'inflow', 'outflow', 'balance')
LIMITS = ('10', '25', '50', 'all')


class BalanceSheetFilters(forms.Form):
    search = forms.CharField(required=False, max_length=150)
    sort_by = forms.ChoiceField(choices=[('id', 'ID'), *COLUMNS.items()])
    sort_dir = forms.ChoiceField(choices=[('asc', 'Ascending'), ('desc', 'Descending')])
    limit = forms.ChoiceField(choices=[(limit, limit) for limit in LIMITS])
    page = forms.IntegerField(min_value=1)
    export = forms.ChoiceField(required=False, choices=[('csv', 'CSV'), ('pdf', 'PDF')])


def totals(rows):
    return {field: sum((row[field] for row in rows), ZERO) for field in AMOUNTS}


@transaction.atomic
def balance_sheet(scope, filters):
    accounts = scoped_queryset(FinancialAccount.objects.filter(
        is_active=True, currency=settings.INVENTORY_BASE_CURRENCY), scope)
    if filters['search']:
        accounts = accounts.filter(name__icontains=filters['search'])
    rows = {}
    for account in accounts.order_by('pk'):
        rows[account.pk] = dict(id=account.pk, name=account.name, type=account.get_type_display(),
                                opening_balance=account.opening_balance, inflow=ZERO, outflow=ZERO)
    postings = AccountTransaction.objects.filter(account__in=accounts).order_by()
    for pk, direction, amount in postings.values_list('account_id', 'direction', 'amount').iterator():
        rows[pk][direction] += amount
    for row in rows.values():
        row['balance'] = row['opening_balance'] + row['inflow'] - row['outflow']

    def sort_value(row):
        value = row[filters['sort_by']]
        return value.casefold() if isinstance(value, str) else value

    ordered = sorted(rows.values(), key=sort_value, reverse=filters['sort_dir'] == 'desc')
    limit = filters['limit']
    pager = Paginator(ordered, max(len(ordered), 1) if limit == 'all' else int(limit))
    page = pager.get_page(filters['page'])
    return dict(results=page.object_list, page_totals=totals(page.object_list), filtered_totals=totals(ordered),
                page=page.number, pages=pager.num_pages, count=pager.count, start=page.start_index(),
                end=page.end_index(), page_numbers=list(pager.get_elided_page_range(page.number, on_each_side=1)),
                currency=settings.INVENTORY_BASE_CURRENCY,
                scope_label='All warehouses and shared accounts' if all_warehouse_access(scope) else str(scope.warehouse))


def table_context(report):
    return dict(columns=COLUMNS.items(),
                rows=[(row['id'], [(key, row[key]) for key in COLUMNS]) for row in report['results']],
                page_amounts=report['page_totals'].values(), filtered_amounts=report['filtered_totals'].values())


def csv_response(report):
    response = HttpResponse(content_type='text/csv; charset=utf-8',
                            headers={'Content-Disposition': 'attachment; filename="balance-sheet.csv"'})
    writer = csv.writer(response)
    writer.writerow(COLUMNS.values())
    for row in report['results']:
        values = []
        for key in COLUMNS:
            value = row[key]
            if key in AMOUNTS:
                value = format(value, '.4f')
            elif value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')):
                value = "'" + value
            values.append(value)
        writer.writerow(values)
    writer.writerow(['Page total', '', *report['page_totals'].values()])
    return response


@method_decorator(never_cache, name='dispatch')
class BalanceSheetView(FinancePageView):
    permission_required = STATEMENT_VIEW
    finance_key = 'balance-sheet'
    template_name = 'accounting/balance_sheet.html'
    json_response = False

    def handle_no_permission(self):
        if self.json_response:
            return JsonResponse({'detail': 'Statement permission and warehouse access are required.'}, status=403)
        return super().handle_no_permission()

    def get(self, request, *args, **kwargs):
        params = request.GET.copy()
        for key, value in dict(sort_by='id', sort_dir='asc', limit='10', page='1').items():
            params.setdefault(key, value)
        form = BalanceSheetFilters(params)
        if not form.is_valid():
            if self.json_response:
                return JsonResponse({'errors': form.errors}, status=400)
            return self.render_to_response(self.get_context_data(form=form, page_label='Balance Sheet'), status=400)

        filters = form.cleaned_data
        report = balance_sheet(warehouse_scope(request.user), filters)
        if filters['export'] == 'csv':
            return csv_response(report)
        if self.json_response and not filters['export']:
            return JsonResponse(report)
        table = table_context(report)
        if filters['export'] == 'pdf':
            try:
                pdf = render_pdf({**report, **table, 'company': {}}, 'accounting/balance_sheet_pdf.html')
            except ReportUnavailable as exc:
                return HttpResponse(str(exc.detail), status=503, content_type='text/plain')
            return HttpResponse(pdf, content_type='application/pdf', headers={
                'Content-Disposition': 'attachment; filename="balance-sheet.pdf"'})
        return self.render_to_response(self.get_context_data(
            page_label='Balance Sheet', report=report, table=table, filters=filters, limits=LIMITS))
