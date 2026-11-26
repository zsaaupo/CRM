import csv
from io import StringIO

from rest_framework.exceptions import ValidationError

from .stock_count_files import csv_response


TRANSFER_CSV_HEADERS = ('sku', 'quantity')
MAX_TRANSFER_CSV_BYTES = 5 * 1024 * 1024
MAX_TRANSFER_CSV_ROWS = 1000
MAX_TRANSFER_CSV_ERRORS = 50


def transfer_csv_template():
    return csv_response('warehouse-transfer-template.csv', TRANSFER_CSV_HEADERS, ())


def parse_transfer_rows(upload):
    """Return the CSV line number, SKU and quantity for each product row."""
    if not upload.name.lower().endswith('.csv'):
        raise ValidationError('Choose a .csv file.')
    if upload.size > MAX_TRANSFER_CSV_BYTES:
        raise ValidationError('Choose a CSV file of 5 MB or less.')

    rows, errors = [], []
    try:
        content = upload.read().decode('utf-8-sig')
        if '\x00' in content:
            raise ValueError('Unexpected null byte')
        reader = csv.reader(StringIO(content, newline=''), strict=True, skipinitialspace=True)
        headers = [cell.strip().lower() for cell in next(reader, ())]
        if sorted(headers) != sorted(TRANSFER_CSV_HEADERS):
            raise ValidationError('Use the CSV headers sku and quantity.')
        for cells in reader:
            if not any(cell.strip() for cell in cells):
                continue
            if len(cells) != len(headers):
                errors.append(f'Row {reader.line_num}: expected two columns.')
                if len(errors) == MAX_TRANSFER_CSV_ERRORS:
                    break
                continue
            values = dict(zip(headers, cells))
            rows.append((reader.line_num, values['sku'].strip(), values['quantity'].strip()))
            if len(rows) > MAX_TRANSFER_CSV_ROWS:
                raise ValidationError(f'Include at most {MAX_TRANSFER_CSV_ROWS:,} product rows per transfer.')
    except (UnicodeError, csv.Error, ValueError) as exc:
        raise ValidationError('The file is malformed. Save it as UTF-8 CSV and try again.') from exc

    if errors:
        raise ValidationError(errors)
    if not rows:
        raise ValidationError('Add at least one product row to the CSV file.')
    return rows
