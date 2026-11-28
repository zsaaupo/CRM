import csv
from io import StringIO

from django.http import HttpResponse
from rest_framework.exceptions import ValidationError


MAX_CSV_BYTES = MAX_PRODUCT_CSV_BYTES = 5 * 1024 * 1024
MAX_CSV_ROWS = MAX_PRODUCT_CSV_ROWS = 1000
MAX_CSV_ERRORS = MAX_PRODUCT_CSV_ERRORS = 50


def csv_response(filename, headers, rows):
    content = StringIO(newline='')
    writer = csv.writer(content)
    writer.writerow(headers)
    writer.writerows(rows)
    return HttpResponse(content.getvalue().encode('utf-8-sig'), content_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})


def read_product_csv(upload, required, *, optional=(), document):
    return read_csv_rows(upload, required, optional=optional, row_label='product', document=document)


def read_csv_rows(upload, required, *, optional=(), row_label, document=None):
    """Read the specified columns with their CSV line numbers."""
    if not upload.name.lower().endswith('.csv'):
        raise ValidationError('Choose a .csv file.')
    if upload.size > MAX_CSV_BYTES:
        raise ValidationError('Choose a CSV file of 5 MB or less.')

    rows, errors = [], []
    try:
        content = upload.read().decode('utf-8-sig')
        if '\x00' in content:
            raise ValueError('Unexpected null byte')
        reader = csv.reader(StringIO(content, newline=''), strict=True, skipinitialspace=True)
        headers = [cell.strip().lower() for cell in next(reader, ())]
        columns = set(headers)
        if len(headers) != len(columns) or not set(required) <= columns <= set((*required, *optional)):
            hint = f' Optional headers are {", ".join(optional)}.' if optional else ''
            raise ValidationError(f'Use the CSV headers {" and ".join(required)}.{hint}')
        for cells in reader:
            if not any(cell.strip() for cell in cells):
                continue
            if len(cells) != len(headers):
                count = 'two' if len(headers) == 2 else str(len(headers))
                errors.append(f'Row {reader.line_num}: expected {count} columns.')
                if len(errors) == MAX_CSV_ERRORS:
                    break
                continue
            rows.append((reader.line_num, dict(zip(headers, (cell.strip() for cell in cells)))))
            if len(rows) > MAX_CSV_ROWS:
                scope = f' per {document}' if document else ''
                raise ValidationError(f'Include at most {MAX_CSV_ROWS:,} {row_label} rows{scope}.')
    except (UnicodeError, csv.Error, ValueError) as exc:
        raise ValidationError('The file is malformed. Save it as UTF-8 CSV and try again.') from exc

    if errors:
        raise ValidationError(errors)
    if not rows:
        raise ValidationError(f'Add at least one {row_label} row to the CSV file.')
    return rows
