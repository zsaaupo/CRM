from hashlib import sha256
from io import BytesIO, StringIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch
from zipfile import ZipFile

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings


@skipUnless(os.name == 'nt', 'The portable runtime installer is Windows-specific.')
class ReportRuntimeSetupTests(SimpleTestCase):
    def archive(self, name):
        output = BytesIO()
        with ZipFile(output, 'w') as package:
            package.writestr(name, b'fixture-library')
        return output.getvalue()

    def test_checksum_failure_does_not_extract_downloaded_files(self):
        with TemporaryDirectory() as temporary, override_settings(BASE_DIR=Path(temporary)):
            with patch('report.management.commands.setup_report_runtime.urlopen', return_value=BytesIO(b'invalid download')):
                with self.assertRaisesRegex(CommandError, 'checksum'):
                    call_command('setup_report_runtime', stdout=StringIO())
            self.assertFalse((Path(temporary) / '.runtime' / 'weasyprint').exists())

    def test_archive_paths_are_checked_before_extraction(self):
        data = self.archive('../outside.txt')
        with TemporaryDirectory() as temporary, override_settings(BASE_DIR=Path(temporary)):
            with patch('report.management.commands.setup_report_runtime.urlopen', return_value=BytesIO(data)), \
                 patch('report.management.commands.setup_report_runtime.SHA256', sha256(data).hexdigest()):
                with self.assertRaisesRegex(CommandError, 'Invalid path'):
                    call_command('setup_report_runtime', stdout=StringIO())
            self.assertFalse((Path(temporary) / '.runtime' / 'outside.txt').exists())
