import csv
from decimal import Decimal
from io import TextIOWrapper

from django.http import HttpResponse
from rest_framework import serializers
from rest_framework.exceptions import ValidationError


class CountedLine(serializers.Serializer):
    product = serializers.IntegerField(min_value=1, max_value=2**63-1)
    quantity = serializers.DecimalField(min_value=Decimal('0'), max_digits=18, decimal_places=6)


COLUMNS = ('count_id', 'product_id', 'sku', 'unit', 'expected', 'counted')


def spreadsheet_text(value):
    if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')):
        return "'" + value
    return value


def count_sheet(count):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="stock-count-{count.pk}.csv"'
    response['Cache-Control'] = 'private, no-store'
    writer = csv.writer(response)
    writer.writerow(COLUMNS)
    for line in count.lines.select_related('product', 'unit').iterator():
        writer.writerow((count.pk, line.product_id, spreadsheet_text(line.product.sku),
                         spreadsheet_text(line.unit.code), line.expected, line.counted))
    return response


def preview_sheet(count, upload):
    if not upload:
        raise ValidationError({'file': 'Choose the completed count sheet.'})
    if count.finalized_at:
        raise ValidationError({'file': 'This count has already been finalized.'})
    lines = {line.product_id: line for line in count.lines.select_related('product', 'unit')}
    reviewed = []
    seen = set()
    try:
        with TextIOWrapper(upload.file, encoding='utf-8-sig', newline='') as stream:
            reader = csv.reader(stream, strict=True)
            if tuple(next(reader, ())) != COLUMNS:
                raise ValidationError({'file': 'Use the header from the downloaded count sheet.'})
            for row in reader:
                if not row:
                    continue
                location = f'Row {reader.line_num}'
                if len(row) != len(COLUMNS) or row[0] != str(count.pk):
                    raise ValidationError({location: 'Use a complete row from this stock count.'})
                item = CountedLine(data={'product': row[1], 'quantity': row[5]})
                if not item.is_valid():
                    raise ValidationError({location: item.errors})
                product = item.validated_data['product']
                line = lines.get(product)
                if line is None or product in seen:
                    raise ValidationError({location: 'Each product must appear once in its original count.'})
                if row[2:4] != [spreadsheet_text(line.product.sku), spreadsheet_text(line.unit.code)]:
                    raise ValidationError({location: 'The product code or unit differs from the count sheet.'})
                quantity = item.validated_data['quantity']
                reviewed.append({'product': product, 'sku': line.product.sku, 'unit': line.unit.code,
                                 'expected': str(line.expected), 'quantity': str(quantity),
                                 'variance': str(quantity - line.expected)})
                seen.add(product)
    except (UnicodeDecodeError, csv.Error) as error:
        raise ValidationError({'file': 'Save the sheet as a valid UTF-8 CSV file.'}) from error
    if seen != lines.keys():
        raise ValidationError({'file': 'Enter a counted quantity for every product on this sheet.'})
    return reviewed
