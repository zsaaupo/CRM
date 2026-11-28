from django.core.management.base import BaseCommand

from hrm.promotions import sync_due_designations


class Command(BaseCommand):
    help = 'Synchronize employee designations with effective changes, including overdue changes.'

    def handle(self, *args, **options):
        count = sync_due_designations()
        self.stdout.write(self.style.SUCCESS(f'Synchronized {count} employee designation(s).'))
