from collections import defaultdict
from itertools import groupby

from django import forms
from django.conf import settings
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, CharField, Q, Value, When
from django.db.models.functions import Cast, Concat
from django.http import JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache

from warehouse.scope import all_warehouse_access, warehouse_scope
from .models import AccountTransaction, FinancialAccount, ZERO
from .page_views import FinancePageView
from .services import STATEMENT_VIEW


SOURCES = {
    'fund_transfer': ('Fund transfer', Q(transfer__transfer_type='fund_transfer')),
    'money_transfer': ('Money transfer', Q(transfer__transfer_type='money_transfer')),
    'sale': ('Sales payment', Q(sales_payment__isnull=False, sales_payment__sales_return__isnull=True)),
    'chalan': ('Chalan payment', Q(chalan_payment__isnull=False)),
    'sales_refund': ('Sales refund', Q(sales_payment__sales_return__isnull=False)),
    'purchase': ('Purchase payment', Q(purchase_payment__isnull=False, purchase_payment__purchase_return__isnull=True)),
    'purchase_refund': ('Purchase refund', Q(purchase_payment__purchase_return__isnull=False)),
    'expense': ('Expense payment', Q(expense_payment__isnull=False)),
    'income': ('Income payment', Q(income_payment__isnull=False)),
}
VOUCHERS = {'transfer': 'TR', 'expense_payment': 'EP', 'income_payment': 'IP', 'sales_payment': 'SP',
            'purchase_payment': 'PP', 'chalan_payment': 'CP'}


class DaybookFilters(forms.Form):
    start_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    end_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    account = forms.ModelChoiceField(queryset=FinancialAccount.objects.none(), required=False, empty_label='All accounts')
    source = forms.ChoiceField(required=False, choices=[('', 'All sources'), *((k, v[0]) for k, v in SOURCES.items())])
    q = forms.CharField(label='Search voucher, account or reference', required=False, max_length=160)
    status = forms.ChoiceField(required=False, choices=[('posted', 'Posted')])
    order = forms.ChoiceField(required=False, choices=[('oldest', 'Oldest first'), ('newest', 'Newest first')])

    def clean(self):
        data = super().clean()
        if data.get('start_date') and data.get('end_date') and data['start_date'] > data['end_date']:
            self.add_error('end_date', 'Choose an end date on or after the start date.')
        return data


def daybook(accounts, filters, page, printing):
    rows = AccountTransaction.objects.filter(account__in=accounts).select_related(
        'account', *VOUCHERS, 'income_payment__income__category', 'expense_payment__expense__category')
    if filters.get('start_date'):
        rows = rows.filter(date__gte=filters['start_date'])
    if filters.get('end_date'):
        rows = rows.filter(date__lte=filters['end_date'])
    if filters['account']:
        rows = rows.filter(account=filters['account'])
    if filters['source']:
        rows = rows.filter(SOURCES[filters['source']][1])
    # Use the actual document numbers so filtering, ordering and grouping agree.
    voucher = Case(*[
        When(**{f'{field}__isnull': False}, then=Concat(Value(prefix + '-'), Cast(f'{field}_id', CharField())))
        for field, prefix in VOUCHERS.items()
    ], output_field=CharField())
    rows = rows.annotate(voucher=voucher)
    if filters['q']:
        term = filters['q']
        match = Q(account__name__icontains=term) | Q(voucher__icontains=term)
        for field in VOUCHERS:
            match |= Q(**{f'{field}__reference__icontains': term})
        match |= Q(income_payment__income__category__name__icontains=term)
        match |= Q(expense_payment__expense__category__name__icontains=term)
        for label, predicate in SOURCES.values():
            if term.casefold() in label.casefold():
                match |= predicate
        rows = rows.filter(match)
    order = '-' if filters['order'] == 'newest' else ''
    rows = rows.order_by(order + 'date', order + 'voucher', 'pk')
    groups = rows.values_list('date', 'voucher').order_by(order + 'date', order + 'voucher').distinct()
    pager = Paginator(groups, 25)
    selected = pager.get_page(page)
    if not printing:
        match = Q(pk__in=[])
        for date, number in selected:
            match |= Q(date=date, voucher=number)
        visible = rows.filter(match)
    else:
        visible = rows
    totals = defaultdict(lambda: ZERO)
    for direction, amount in rows.order_by().values_list('direction', 'amount').iterator():
        totals[direction] += amount
    entries = []
    for (date, number), lines in groupby(visible.iterator(), key=lambda r: (r.date, r.voucher)):
        lines = list(lines)
        first = lines[0]
        source = next(getattr(first, field) for field in VOUCHERS if getattr(first, field + '_id'))
        if first.transfer_id:
            label = source.get_transfer_type_display()
            narration = label
        elif first.income_payment_id:
            label = 'Income payment'
            narration = source.income.category.name
        elif first.expense_payment_id:
            label = 'Expense payment'
            narration = source.expense.category.name
        else:
            label = source.type_label
            narration = label
        entry = dict(date=date, voucher=number, source=label, reference=source.reference, narration=narration,
                     created_by=source.create_by, lines=[], inflow=ZERO, outflow=ZERO)
        for line in lines:
            entry[line.direction] += line.amount
            entry['lines'].append(dict(account=line.account_id, name=line.account.name, posted_by=line.create_by,
                                       posted_at=line.created_at, direction=line.direction, amount=line.amount))
        entries.append(entry)
    return dict(entries=entries, inflow=totals['inflow'], outflow=totals['outflow'],
                net=totals['inflow'] - totals['outflow'], page=selected.number,
                pages=pager.num_pages, count=pager.count, start=filters.get('start_date'), end=filters.get('end_date'))


@method_decorator(never_cache, name='dispatch')
class DaybookView(FinancePageView):
    permission_required = STATEMENT_VIEW
    template_name = 'accounting/daybook.html'
    finance_key = 'daybook'
    json_response = False

    def handle_no_permission(self):
        if self.json_response:
            return JsonResponse({'detail': 'Statement permission and warehouse access are required.'}, status=403)
        return super().handle_no_permission()

    @transaction.atomic
    def get(self, request, *args, **kwargs):
        accounts = self.accounts().filter(currency=settings.INVENTORY_BASE_CURRENCY)
        today = timezone.localdate()
        params = request.GET.copy()
        if not any(k in params for k in ('start_date', 'end_date')):
            params.update({'start_date': today.isoformat(), 'end_date': today.isoformat()})
        form = DaybookFilters(params)
        form.fields['account'].queryset = accounts
        for field in form.fields.values():
            field.widget.attrs['class'] = 'form-input'
        printing = request.GET.get('print') == '1'
        report = None
        if form.is_valid():
            report = daybook(accounts, form.cleaned_data, params.get('page'), printing)
        scope = warehouse_scope(request.user)
        metadata = dict(currency=settings.INVENTORY_BASE_CURRENCY, generated_at=timezone.now(),
                        scope_label='All warehouses and shared accounts' if all_warehouse_access(scope) else str(scope.warehouse))
        if self.json_response:
            if report is None:
                return JsonResponse({'errors': form.errors}, status=400)
            return JsonResponse({**metadata, **report})
        context = self.get_context_data(**kwargs)
        params.pop('page', None)
        params.pop('print', None)
        context.update(**metadata, form=form, report=report, printing=printing,
                       query=params.urlencode(), page_label='Daybook')
        return self.render_to_response(context)
