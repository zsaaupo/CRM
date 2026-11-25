from datetime import date

from django.core.management.base import BaseCommand
from django.utils import timezone

from hrm.leave_policies import expire_carry_forward


class Command(BaseCommand):
    help = 'Expire unconsumed carried-forward leave balances after the expiry cutoff.'

    def add_arguments(self, parser):
        parser.add_argument('--year', type=int, default=timezone.localdate().year)
        parser.add_argument('--as-of', type=str, default=None)

    def handle(self, *args, **options):
        as_of = date.fromisoformat(options['as_of']) if options['as_of'] else None
        count = expire_carry_forward(options['year'], as_of_date=as_of)
        self.stdout.write(self.style.SUCCESS(f'Expired carry-forward on {count} balance(s).'))
