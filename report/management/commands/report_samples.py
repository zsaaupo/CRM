from pathlib import Path

from django.core.management.base import BaseCommand

from report.demo import demo_document
from report.rendering import render_pdf


class Command(BaseCommand):
    help = 'Generate three synthetic report previews without changing database data.'

    def add_arguments(self, parser):
        parser.add_argument('--output-dir', default='output/pdf')

    def handle(self, *args, **options):
        target = Path(options['output_dir'])
        target.mkdir(parents=True, exist_ok=True)
        for kind in ['purchase', 'sale', 'transfer']:
            path = target / f'{kind}-sample.pdf'
            path.write_bytes(render_pdf(demo_document(kind)))
            self.stdout.write(self.style.SUCCESS(str(path.resolve())))
