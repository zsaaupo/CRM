from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from hrm.leave_balances import prepare_leave_balances


class Command(BaseCommand):
    help = 'Allocate annual paid-leave quotas and optionally finalize carry or reconcile existing approved leave.'

    def add_arguments(self, parser):
        parser.add_argument('--year', type=int, default=None)
        parser.add_argument('--finalize-carry', action='store_true')
        parser.add_argument('--reconcile-approved', action='store_true')

    def handle(self, *args, **options):
        year = options['year'] if options['year'] is not None else timezone.localdate().year
        if not 1 <= year <= 9999:
            raise CommandError('Choose a year between 1 and 9999.')
        try:
            counts = prepare_leave_balances(year, 'allocate_leave_balances', finalize_carry=options['finalize_carry'],
                                           reconcile=options['reconcile_approved'])
        except ValidationError as exc:
            raise CommandError(' '.join(exc.messages)) from exc
        self.stdout.write(self.style.SUCCESS(f'Leave balances for {year}: ' + ', '.join(
            f'{label.replace("_", " ")} {count}' for label, count in counts.items())))
