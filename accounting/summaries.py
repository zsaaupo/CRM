from collections import defaultdict

from django.db import transaction
from django.http import JsonResponse

from account.page_views import ProtectedAccountPageView
from account.permissions import has_role_permissions
from report.trading import Period
from warehouse.models import WareHouse
from warehouse.scope import has_warehouse_scope, scoped_queryset, warehouse_scope
from .models import AccountTransaction, Expense, FinancialAccount, Income, ZERO, base_currency
from .services import STATEMENT_VIEW


def account_summary(scope, branch, start, end):
    accounts = scoped_queryset(FinancialAccount.objects.all(), scope)
    if branch:
        accounts = accounts.filter(warehouse=branch)
    rows = {pk: dict(name=name, opening=amount, received=ZERO, sent=ZERO) for pk, name, amount in
            accounts.values_list('pk', 'name', 'opening_balance')}
    entries = AccountTransaction.objects.filter(account__in=accounts, date__lte=end).order_by().values_list(
        'account_id', 'date', 'direction', 'amount')
    for account, date, direction, amount in entries.iterator():
        inflow = direction == AccountTransaction.Direction.INFLOW
        if date < start:
            rows[account]['opening'] += amount if inflow else -amount
        else:
            rows[account]['received' if inflow else 'sent'] += amount
    totals = dict(opening=ZERO, received=ZERO, sent=ZERO, closing=ZERO)
    for row in rows.values():
        row['closing'] = row['opening'] + row['received'] - row['sent']
        for key in totals:
            totals[key] += row[key]
    return dict(rows=list(rows.values()), totals=totals)


def category_summary(scope, branch, start, end):
    rows = []
    totals = {}
    for kind, model in [('income', Income), ('expense', Expense)]:
        records = scoped_queryset(model.objects.filter(is_completed=True, date__range=(start, end)), scope)
        if branch:
            records = records.filter(warehouse=branch)
        amounts = defaultdict(lambda: ZERO)
        for category, amount in records.order_by().values_list('category__name', 'amount').iterator():
            amounts[category] += amount
        rows.extend(dict(kind=kind, category=k, amount=v) for k, v in sorted(amounts.items()))
        totals[kind] = sum(amounts.values(), ZERO)
    return dict(rows=rows, totals={**totals, 'difference': totals['income'] - totals['expense']})


class FinancialSummaryView(ProtectedAccountPageView):
    template_name = 'accounting/summary.html'
    kind = 'balances'
    as_json = False

    def has_permission(self):
        perms = STATEMENT_VIEW if self.kind == 'balances' else ('accounting.view_income', 'accounting.view_expense')
        return has_warehouse_scope(self.request.user) and has_role_permissions(self.request.user, perms)

    @transaction.atomic
    def get(self, request):
        scope = warehouse_scope(request.user)
        form = Period(request.GET)
        form.fields['warehouse'].queryset = scoped_queryset(WareHouse.objects.all(), scope, 'pk')
        data = {}
        valid = form.is_valid()
        if valid:
            fields = form.cleaned_data
            summarize = account_summary if self.kind == 'balances' else category_summary
            data = summarize(scope, fields['warehouse'], fields['start_date'], fields['end_date'])
            data.update(currency=base_currency(), start=fields['start_date'], end=fields['end_date'])
        if self.as_json:
            return JsonResponse(data if valid else {'errors': form.errors.get_json_data()}, status=200 if valid else 400)
        return self.render_to_response(self.get_context_data(form=form, kind=self.kind, **data), status=200 if valid else 400)
