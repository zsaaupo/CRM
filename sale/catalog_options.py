from inventory.models import Currency, Product


def sales_currency_options():
    return [
        {'value': row.code, 'label': f'{row.name} ({row.code})',
         'exchange_rate': str(row.exchange_rate) if row.exchange_rate is not None else None}
        for row in Currency.objects.order_by('name', 'pk')
    ]


def sales_product_options(*, include_unit_name=True):
    return [
        {'value': row.pk, 'label': f'{row.sku} — {row.name}', 'unit': row.unit_id,
         'unit_label': f'{row.unit.name} ({row.unit.code})' if include_unit_name else row.unit.code,
         'currency': row.currency_id, 'sale_price': str(row.sale_price),
         'minimum_sale_price': str(row.minimum_sale_price) if row.minimum_sale_price is not None else None,
         'tax_rate': str(row.product_tax)}
        for row in Product.objects.regular_products().select_related('unit').order_by('name', 'pk')
    ]
