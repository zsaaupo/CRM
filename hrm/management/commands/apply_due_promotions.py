from datetime import date

from django.core.management.base import BaseCommand

from hrm.workforce import apply_due_promotions


class Command(BaseCommand):
    help = 'Apply all due EmployeePromotion rows up to today (or --date YYYY-MM-DD).'

    def add_arguments(self, parser):
        parser.add_argument('--date', type=str, default=None, help='Cutoff date YYYY-MM-DD')

    def handle(self, *args, **options):
        cutoff = date.fromisoformat(options['date']) if options['date'] else None
        count = apply_due_promotions(as_of_date=cutoff)
        self.stdout.write(self.style.SUCCESS(f'Applied {count} due promotion(s).'))
