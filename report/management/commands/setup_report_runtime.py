"""Install the pinned official Windows native runtime inside the project only."""
import hashlib
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from zipfile import ZipFile

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


URL = 'https://github.com/Kozea/WeasyPrint/releases/download/v70.0/weasyprint-windows-onedir.zip'
SHA256 = 'ab1151f210b4e6bb7aa7a79e91a67e8ddb760094c107bfda55241b6aaefe7d53'


class Command(BaseCommand):
    help = 'Set up the official WeasyPrint 70.0 Windows runtime under .runtime/weasyprint.'

    def handle(self, *args, **options):
        if os.name != 'nt':
            raise CommandError('This helper is for Windows. Install Pango using your operating system package manager.')
        target = (Path(settings.BASE_DIR) / '.runtime' / 'weasyprint').resolve()
        library = target / 'onedir' / 'weasyprint' / '_internal' / 'libgobject-2.0-0.dll'
        if library.exists():
            self.stdout.write(self.style.SUCCESS(f'Report runtime already available: {library.parent}'))
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=target.parent) as temporary:
            archive = Path(temporary) / 'runtime.zip'
            self.stdout.write('Downloading the official Windows report runtime...')
            try:
                with urlopen(URL, timeout=60) as response, archive.open('wb') as output:
                    shutil.copyfileobj(response, output)
            except OSError as exc:
                raise CommandError('Could not download the report runtime. Check the network and retry.') from exc
            with archive.open('rb') as downloaded:
                checksum = hashlib.file_digest(downloaded, 'sha256').hexdigest()
            if checksum != SHA256:
                raise CommandError('Runtime checksum mismatch. The downloaded archive was not installed.')
            with ZipFile(archive) as package:
                for entry in package.infolist():
                    if not (target / entry.filename).resolve().is_relative_to(target):
                        raise CommandError('Invalid path in runtime archive.')
                package.extractall(target)
        if not library.exists():
            raise CommandError('The runtime archive did not contain the expected libraries.')
        self.stdout.write(self.style.SUCCESS(f'Report runtime installed: {library.parent}'))
