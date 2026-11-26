"""CSV count sheets and exports for the stock count list."""
import csv
from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO, StringIO
from itertools import chain
import re
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZIP_DEFLATED, ZipFile

from django.http import HttpResponse
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from common.csv_files import csv_response

COUNT_HEADERS = ('Product Name', 'Product Code', 'IMEI or Serial Numbers', 'Expected', 'Counted')
MAX_COUNT_FILE = 5 * 1024 * 1024
QUANTITY = serializers.DecimalField(max_digits=18, decimal_places=6, min_value=Decimal(0))
FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r', '\n', '＝', '＋', '－', '＠')
INVALID_XML_TEXT = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]')


@dataclass(frozen=True)
class CountEntry:
    row: int
    sku: str
    counted: Decimal
    expected: Decimal | None = None


def csv_text(value):
    # Spreadsheet applications can evaluate cells that begin with a formula marker.
    if value.startswith(("'", *FORMULA_PREFIXES)) or value.lstrip().startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def csv_sku(value):
    if value.startswith("'") and csv_text(value[1:]) == value:
        return value[1:]
    return value


def decimal_text(value):
    if value is None:
        return ''
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def parse_count_file(upload):
    if upload.size > MAX_COUNT_FILE:
        raise ValidationError({'file': 'Choose a CSV file of 5 MB or less.'})
    if not upload.name.lower().endswith('.csv'):
        raise ValidationError({'file': 'Choose a CSV count sheet.'})
    entries, errors = [], []
    try:
        text = upload.read().decode('utf-8-sig')
        if '\x00' in text:
            raise ValueError('Unexpected null byte')
        reader = csv.reader(StringIO(text, newline=''), strict=True, skipinitialspace=True)
        columns = tuple(header.strip().lower() for header in next(reader, []))
        headers = {header.lower() for header in COUNT_HEADERS}
        if len(columns) != len(headers) or set(columns) != headers:
            raise ValidationError({'file': ['Use the count sheet headers: ' + ', '.join(COUNT_HEADERS) + '.']})
        for row in reader:
            if len(errors) >= 50:
                break
            if not row or not any(cell.strip() for cell in row):
                continue
            if len(row) != len(columns):
                errors.append(f'Row {reader.line_num}: expected {len(columns)} columns.')
            else:
                values = dict(zip(columns, row))
                sku = csv_sku(values['product code']).strip()
                expected = values['expected'].strip()
                if not sku or len(sku) > 64:
                    errors.append(f'Row {reader.line_num}: enter a valid Product Code.')
                    continue
                if values['imei or serial numbers'].strip().lower() not in ('', 'null'):
                    errors.append(f'Row {reader.line_num} ({sku}): enter quantities per SKU and clear serial numbers.')
                    continue
                try:
                    counted = QUANTITY.run_validation(values['counted'].strip())
                    baseline = QUANTITY.run_validation(expected) if expected else None
                except ValidationError:
                    errors.append(f'Row {reader.line_num} ({sku}): enter a nonnegative Counted number and use '
                                  'the original Expected value. Quantities support up to six decimal places.')
                else:
                    entries.append(CountEntry(reader.line_num, sku, counted, baseline))
    except (UnicodeError, csv.Error, ValueError) as exc:
        raise ValidationError({'file': 'The file is malformed. Save the count sheet as UTF-8 CSV.'}) from exc
    if errors:
        raise ValidationError({'items': errors[:50]})
    if not entries:
        raise ValidationError({'file': 'The count sheet has no product rows.'})
    return entries


def count_sheet(count, *, final=False):
    rows = (
        (csv_text(item.product_name), csv_text(item.sku), '',
         decimal_text(item.expected_qty) if final or not count.blind_count else '',
         decimal_text(item.counted_qty) if final else '')
        for item in count.items.all()
    )
    return csv_response(f'{count.reference_no}-{"counted" if final else "initial"}.csv', COUNT_HEADERS, rows)


def xlsx_response(filename, headers, rows, *, sheet_name='Stock counts'):
    """Write a single sheet with explicit text cells for references and labels."""
    spreadsheet = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    office = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    package = 'http://schemas.openxmlformats.org/package/2006'
    workbook = Element('workbook', {'xmlns': spreadsheet, 'xmlns:r': office})
    SubElement(SubElement(workbook, 'sheets'), 'sheet', name=sheet_name, sheetId='1', attrib={'r:id': 'rId1'})
    sheet = Element('worksheet', {'xmlns': spreadsheet})
    columns = SubElement(sheet, 'cols')
    for number, cells in enumerate(zip(headers, *rows), start=1):
        width = min(55, max(len(str(value)) for value in cells) + 3)
        SubElement(columns, 'col', min=str(number), max=str(number), width=str(width), customWidth='1')
    data = SubElement(sheet, 'sheetData')
    for values in chain((headers,), rows):
        row = SubElement(data, 'row')
        for value in values:
            if isinstance(value, int):
                SubElement(SubElement(row, 'c', t='n'), 'v').text = str(value)
            else:
                cell = SubElement(row, 'c', t='inlineStr')
                SubElement(SubElement(cell, 'is'), 't', {'xml:space': 'preserve'}).text = INVALID_XML_TEXT.sub('', value)

    content_types = Element('Types', {'xmlns': f'{package}/content-types'})
    SubElement(content_types, 'Default', Extension='rels',
               ContentType='application/vnd.openxmlformats-package.relationships+xml')
    SubElement(content_types, 'Default', Extension='xml', ContentType='application/xml')
    for part, kind in [('xl/workbook.xml', 'sheet.main'), ('xl/worksheets/sheet1.xml', 'worksheet')]:
        SubElement(content_types, 'Override', PartName=f'/{part}',
                   ContentType=f'application/vnd.openxmlformats-officedocument.spreadsheetml.{kind}+xml')

    def relationships(kind, target):
        root = Element('Relationships', {'xmlns': f'{package}/relationships'})
        SubElement(root, 'Relationship', Id='rId1', Type=f'{office}/{kind}', Target=target)
        return root

    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for path, element in {
            '[Content_Types].xml': content_types,
            '_rels/.rels': relationships('officeDocument', 'xl/workbook.xml'),
            'xl/workbook.xml': workbook,
            'xl/_rels/workbook.xml.rels': relationships('worksheet', 'worksheets/sheet1.xml'),
            'xl/worksheets/sheet1.xml': sheet,
        }.items():
            archive.writestr(path, tostring(element, encoding='utf-8', xml_declaration=True))
    return HttpResponse(output.getvalue(),
                        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})
