from rest_framework import serializers

from common.csv_files import MAX_PRODUCT_CSV_ERRORS, csv_response, read_product_csv

from .models import Product


REQUIRED_COLUMNS = ('sku', 'quantity')
OPTIONAL_COLUMNS = ('unit_price', 'discount_amount', 'tax_rate', 'description')


def order_csv_template(filename):
    return csv_response(filename, (*REQUIRED_COLUMNS, *OPTIONAL_COLUMNS), ())


def order_csv_items(upload, currency, *, document, line_serializer, price_field, tax_field=None):
    try:
        rows = read_product_csv(upload, REQUIRED_COLUMNS, optional=OPTIONAL_COLUMNS, document=document)
    except serializers.ValidationError as exc:
        raise serializers.ValidationError({'file': exc.detail}) from exc

    sku_length = Product._meta.get_field('sku').max_length
    skus = {row['sku'] for _, row in rows if 0 < len(row['sku']) <= sku_length}
    products = Product.objects.in_bulk(skus, field_name='sku')
    items, errors = [], []
    for line_number, (csv_row, row) in enumerate(rows, start=1):
        sku = row['sku']
        product = products.get(sku)
        price = row.get('unit_price')
        if product is None:
            detail = f'Unknown SKU {sku!r}.' if sku in skus else f'Enter a SKU of up to {sku_length} characters.'
            errors.append(f'Row {csv_row}: {detail}')
        elif not price and product.currency_id != currency:
            errors.append(f'Row {csv_row}: Enter a unit price in {currency} for SKU {sku!r}.')
        else:
            default_tax = getattr(product, tax_field) if tax_field else 0
            try:
                item = line_serializer.run_validation({
                    'line_number': line_number, 'product': product.pk, 'unit': product.unit_id,
                    'quantity': row['quantity'], 'unit_price': price or getattr(product, price_field),
                    'discount_amount': row.get('discount_amount') or 0,
                    'tax_rate': row.get('tax_rate') or default_tax,
                    'description': row.get('description', ''),
                })
            except serializers.ValidationError as exc:
                details = ' '.join(f'{name.replace("_", " ")}: {" ".join(messages)}'
                                   for name, messages in exc.detail.items())
                errors.append(f'Row {csv_row}: {details}')
            else:
                items.append(item)
        if len(errors) == MAX_PRODUCT_CSV_ERRORS:
            break
    if errors:
        raise serializers.ValidationError({'file': errors})
    return items
