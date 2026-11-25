from decimal import Decimal
import json

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Case, DecimalField, F, Sum, Value, When

from accounting.models import AccountTransaction, FinancialAccount
from accounting.services import accounts_with_balances


ZERO = Decimal('0.0000')


class Command(BaseCommand):
    help = 'Reconcile stored account opening/transaction balances against AccountTransaction ledger rows.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', dest='as_json', help='Emit machine-readable JSON output.')

    def handle(self, *args, **options):
        annotated = {
            row.pk: Decimal(row.balance)
            for row in accounts_with_balances(FinancialAccount.objects.all())
        }
        mismatches = []
        checked = 0

        for acct in FinancialAccount.objects.order_by('pk'):
            checked += 1
            agg = AccountTransaction.objects.filter(account=acct).aggregate(
                inflow=Sum(
                    Case(
                        When(direction=AccountTransaction.Direction.INFLOW, then=F('amount')),
                        default=Value(ZERO),
                        output_field=DecimalField(max_digits=15, decimal_places=4),
                    ),
                ),
                outflow=Sum(
                    Case(
                        When(direction=AccountTransaction.Direction.OUTFLOW, then=F('amount')),
                        default=Value(ZERO),
                        output_field=DecimalField(max_digits=15, decimal_places=4),
                    ),
                ),
            )
            inflow = Decimal(agg['inflow'] or ZERO)
            outflow = Decimal(agg['outflow'] or ZERO)
            expected = Decimal(acct.opening_balance) + inflow - outflow
            latest_bal = annotated.get(acct.pk, Decimal(acct.opening_balance))
            if expected != latest_bal:
                mismatches.append({
                    'account_id': acct.pk,
                    'name': acct.name,
                    'opening_balance': str(acct.opening_balance),
                    'expected_balance': str(expected),
                    'latest_balance': str(latest_bal),
                    'delta': str(latest_bal - expected),
                })

        if options.get('as_json'):
            self.stdout.write(json.dumps({'checked': checked, 'mismatches': mismatches}))
        if mismatches:
            raise CommandError(
                f'Account balance reconciliation failed: {len(mismatches)} mismatch(es) across {checked} account(s).'
            )
        if not options.get('as_json'):
            self.stdout.write(self.style.SUCCESS(
                f'Account balances reconciled: 0 mismatches across {checked} account(s).'
            ))
