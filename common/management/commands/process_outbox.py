from django.core.management.base import BaseCommand

from common.ops_services import process_outbox_messages


class Command(BaseCommand):
    help = 'Process pending SMS/mail outbox messages with retry backoff.'

    def add_arguments(self, parser):
        parser.add_argument('--max-attempts', type=int, default=5)

    def handle(self, *args, **options):
        res = process_outbox_messages(max_attempts=options['max_attempts'])
        self.stdout.write(self.style.SUCCESS(f"Processed {res['processed']} (sent={res['sent']}, failed={res['failed']})."))
