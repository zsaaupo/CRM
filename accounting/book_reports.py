from collections import defaultdict
from datetime import date
from decimal import Decimal
from itertools import groupby

from django import forms
from django.conf import settings
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, CharField, Q, Sum, Value, When
from django.db.models.functions import Cast, Concat
from django.http import JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.exceptions import PermissionDenied
from rest_framework.views import APIView
from rest_framework.response import Response

from account.permissions import has_role_permissions
from common.money import convert_to_base, q_money
from common.view import BaseAPIMixin
from warehouse.scope import all_warehouse_access, scoped_queryset, warehouse_scope
from .models import AccountTransaction, DepositEntry, Expense, FinancialAccount, Income, ZERO
from .page_views import FinancePageView
from .services import STATEMENT_VIEW, available_payment_accounts, compute_order_balance


SOURCES = {
    'fund_transfer': ('Fund transfer', Q(transfer__transfer_type='fund_transfer')),
    'money_transfer': ('Money transfer', Q(transfer__transfer_type='money_transfer')),
    'sale': ('Sales payment', Q(sales_payment__isnull=False, sales_payment__sales_return__isnull=True)),
    'sales_refund': ('Sales refund', Q(sales_payment__sales_return__isnull=False)),
    'purchase': ('Purchase payment', Q(purchase_payment__isnull=False, purchase_payment__purchase_return__isnull=True)),
    'purchase_refund': ('Purchase refund', Q(purchase_payment__purchase_return__isnull=False)),
    'expense': ('Expense payment', Q(expense_payment__isnull=False)),
    'income': ('Income payment', Q(income_payment__isnull=False)),
}
VOUCHERS = {'transfer': 'TR', 'expense_payment': 'EP', 'income_payment': 'IP', 'sales_payment': 'SP', 'purchase_payment': 'PP'}


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
    ], default=Concat(Value('TX-'), Cast('pk', CharField())), output_field=CharField())
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
        source = next((getattr(first, field) for field in VOUCHERS if getattr(first, field + '_id')), None)
        if first.transfer_id:
            label = source.get_transfer_type_display()
            narration = label
        elif first.income_payment_id:
            label = 'Income payment'
            narration = source.income.category.name
        elif first.expense_payment_id:
            label = 'Expense payment'
            narration = source.expense.category.name
        elif source is not None:
            label = source.type_label
            narration = label
        else:
            label = first.get_source_kind_display()
            narration = label
        entry = dict(date=date, voucher=number, source=label,
                     reference=source.reference if source is not None else first.source_reference,
                     narration=narration, created_by=source.create_by if source is not None else first.create_by,
                     lines=[], inflow=ZERO, outflow=ZERO)
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


# ---------------- FIN-02: Account Balances, Derived Balance Sheet, Income & Expense Report ----------------


def account_balances_report(accounts_qs, *, as_of: date | None = None) -> dict:
    """Separate Opening column (not folded into Credit); transfers appear per account and net to 0 across included accounts."""
    tx_filter = Q()
    if as_of is not None:
        tx_filter = Q(date__lte=as_of)

    accounts = list(accounts_qs.order_by('name', 'pk'))
    tx_sums = defaultdict(lambda: {'inflow': ZERO, 'outflow': ZERO})
    for acct_id, direction, total in (
        AccountTransaction.objects.filter(account__in=accounts)
        .filter(tx_filter)
        .values('account_id', 'direction')
        .annotate(total=Sum('amount'))
        .values_list('account_id', 'direction', 'total')
    ):
        tx_sums[acct_id][direction] = Decimal(total or 0)

    rows = []
    tot_open = ZERO
    tot_credit = ZERO
    tot_debit = ZERO
    tot_bal = ZERO
    for acc in accounts:
        op = Decimal(acc.opening_balance or 0)
        cred = tx_sums[acc.pk]['inflow']
        deb = tx_sums[acc.pk]['outflow']
        bal = op + cred - deb
        tot_open += op
        tot_credit += cred
        tot_debit += deb
        tot_bal += bal
        rows.append({
            'id': acc.pk,
            'name': acc.name,
            'account_number': acc.account_number,
            'currency': acc.currency,
            'warehouse': acc.warehouse_id,
            'opening': str(q_money(op, digits=4)),
            'credit': str(q_money(cred, digits=4)),
            'debit': str(q_money(deb, digits=4)),
            'balance': str(q_money(bal, digits=4)),
        })
    return {
        'as_of': as_of.isoformat() if as_of else 'current',
        'rows': rows,
        'totals': {
            'opening': str(q_money(tot_open, digits=4)),
            'credit': str(q_money(tot_credit, digits=4)),
            'debit': str(q_money(tot_debit, digits=4)),
            'balance': str(q_money(tot_bal, digits=4)),
        },
    }


def balance_sheet_report(actor, scope, *, as_of: date | None = None) -> dict:
    from purchase.models import PurchaseOrder
    from sale.models import GiftCard, GiftCardEntry, SalesOrder
    from warehouse.models import Stock

    accounts = available_payment_accounts(scope).filter(currency=settings.INVENTORY_BASE_CURRENCY)
    acct_rep = account_balances_report(accounts, as_of=as_of)
    cash_and_bank = Decimal(acct_rep['totals']['balance'])

    stocks = scoped_queryset(Stock.objects.all(), scope, lookup='warehouse_id')
    inventory_val = ZERO
    for qty, avg in stocks.values_list('quantity', 'average_cost').iterator():
        inventory_val += Decimal(qty or 0) * Decimal(avg or 0)
    inventory_val = q_money(inventory_val, digits=4)

    so_qs = scoped_queryset(SalesOrder.objects.filter(is_completed=True), scope, lookup='warehouse_id')
    if as_of is not None:
        so_qs = so_qs.filter(order_date__lte=as_of)
    receivables = ZERO
    for so in so_qs.iterator():
        receivables += compute_order_balance(so, as_of=as_of).open_due

    po_qs = scoped_queryset(
        PurchaseOrder.objects.filter(Q(is_received=True) | Q(receipt_state__in=['partial', 'received'])),
        scope, lookup='warehouse_id',
    )
    if as_of is not None:
        po_qs = po_qs.filter(order_date__lte=as_of)
    payables = ZERO
    for po in po_qs.iterator():
        payables += compute_order_balance(po, as_of=as_of).open_due

    dep_qs = DepositEntry.objects.all()
    if as_of is not None:
        dep_qs = dep_qs.filter(created_at__date__lte=as_of)
    dep_cred = dep_qs.filter(kind=DepositEntry.Kind.CREDIT).aggregate(s=Sum('amount'))['s'] or ZERO
    dep_deb = dep_qs.filter(kind=DepositEntry.Kind.DEBIT).aggregate(s=Sum('amount'))['s'] or ZERO
    customer_deposits = q_money(dep_cred - dep_deb, digits=4)

    if as_of is not None:
        gc_liab = q_money(
            GiftCardEntry.objects.filter(created_at__date__lte=as_of).aggregate(s=Sum('amount'))['s'] or ZERO,
            digits=4,
        )
    else:
        gc_liab = q_money(
            GiftCard.objects.filter(is_active=True).aggregate(s=Sum('balance'))['s'] or ZERO,
            digits=4,
        )

    total_assets = q_money(cash_and_bank + inventory_val + receivables, digits=4)
    total_liabilities = q_money(payables + customer_deposits + gc_liab, digits=4)
    equity = q_money(total_assets - total_liabilities, digits=4)

    return {
        'label': 'derived',
        'as_of': as_of.isoformat() if as_of else 'current',
        'assets': {
            'cash_and_bank': str(cash_and_bank),
            'inventory_valuation': str(inventory_val),
            'receivables': str(q_money(receivables, digits=4)),
            'total': str(total_assets),
        },
        'liabilities': {
            'payables': str(q_money(payables, digits=4)),
            'customer_deposits': str(customer_deposits),
            'gift_card_liability': str(gc_liab),
            'total': str(total_liabilities),
        },
        'equity': str(equity),
    }


def income_expense_report(scope, *, start_date: date | None = None, end_date: date | None = None,
                          account_id: int | None = None) -> dict:
    inc_qs = scoped_queryset(Income.objects.filter(is_completed=True).select_related('category'), scope, lookup='warehouse_id')
    exp_qs = scoped_queryset(Expense.objects.filter(is_completed=True).select_related('category'), scope, lookup='warehouse_id')
    if start_date:
        inc_qs = inc_qs.filter(date__gte=start_date)
        exp_qs = exp_qs.filter(date__gte=start_date)
    if end_date:
        inc_qs = inc_qs.filter(date__lte=end_date)
        exp_qs = exp_qs.filter(date__lte=end_date)
    if account_id:
        inc_qs = inc_qs.filter(Q(payment_account_id=account_id) | Q(payments__account_id=account_id)).distinct()
        exp_qs = exp_qs.filter(Q(payment_account_id=account_id) | Q(payments__account_id=account_id)).distinct()

    inc_by_cat = defaultdict(lambda: ZERO)
    exp_by_cat = defaultdict(lambda: ZERO)
    inc_by_period = defaultdict(lambda: ZERO)
    exp_by_period = defaultdict(lambda: ZERO)
    total_inc = ZERO
    total_exp = ZERO

    for row in inc_qs.iterator():
        amt = Decimal(row.amount)
        inc_by_cat[row.category.name] += amt
        inc_by_period[row.date.strftime('%Y-%m')] += amt
        total_inc += amt

    for row in exp_qs.iterator():
        amt = Decimal(row.amount)
        exp_by_cat[row.category.name] += amt
        exp_by_period[row.date.strftime('%Y-%m')] += amt
        total_exp += amt

    periods = sorted(set(inc_by_period.keys()) | set(exp_by_period.keys()))
    return {
        'start_date': start_date.isoformat() if start_date else None,
        'end_date': end_date.isoformat() if end_date else None,
        'income_by_category': [{'category': k, 'amount': str(q_money(v, digits=4))} for k, v in sorted(inc_by_cat.items())],
        'expense_by_category': [{'category': k, 'amount': str(q_money(v, digits=4))} for k, v in sorted(exp_by_cat.items())],
        'by_period': [
            {
                'period': p,
                'income': str(q_money(inc_by_period[p], digits=4)),
                'expense': str(q_money(exp_by_period[p], digits=4)),
                'net': str(q_money(inc_by_period[p] - exp_by_period[p], digits=4)),
            }
            for p in periods
        ],
        'total_income': str(q_money(total_inc, digits=4)),
        'total_expense': str(q_money(total_exp, digits=4)),
        'net': str(q_money(total_inc - total_exp, digits=4)),
    }


class AccountBalancesAPIView(BaseAPIMixin, APIView):
    required_permissions = STATEMENT_VIEW
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        scope = warehouse_scope(request.user)
        as_of = date.fromisoformat(request.query_params['as_of']) if request.query_params.get('as_of') else None
        accounts = available_payment_accounts(scope)
        if request.query_params.get('currency'):
            accounts = accounts.filter(currency=request.query_params['currency'])
        return Response(account_balances_report(accounts, as_of=as_of))


class BalanceSheetAPIView(BaseAPIMixin, APIView):
    required_permissions = ('accounting.view_balance_sheet',)
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        scope = warehouse_scope(request.user)
        as_of = date.fromisoformat(request.query_params['as_of']) if request.query_params.get('as_of') else None
        return Response(balance_sheet_report(request.user, scope, as_of=as_of))


class IncomeExpenseReportAPIView(BaseAPIMixin, APIView):
    required_permissions = STATEMENT_VIEW
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        scope = warehouse_scope(request.user)
        start_d = date.fromisoformat(request.query_params['start_date']) if request.query_params.get('start_date') else None
        end_d = date.fromisoformat(request.query_params['end_date']) if request.query_params.get('end_date') else None
        acct_id = int(request.query_params['account']) if request.query_params.get('account') else None
        return Response(income_expense_report(scope, start_date=start_d, end_date=end_d, account_id=acct_id))
