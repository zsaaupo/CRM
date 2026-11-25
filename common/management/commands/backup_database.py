from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from common.ops_services import run_database_backup


class Command(BaseCommand):
    help = 'Create a timestamped database backup when enabled.'

    def handle(self, *args, **options):
        su = get_user_model().objects.filter(is_superuser=True).order_by('pk').first()
        res = run_database_backup(su)
        self.stdout.write(self.style.SUCCESS(f"Backup written: {res['file']}"))
