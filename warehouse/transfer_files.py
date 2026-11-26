from common.csv_files import (
    MAX_PRODUCT_CSV_BYTES as MAX_TRANSFER_CSV_BYTES,
    MAX_PRODUCT_CSV_ERRORS as MAX_TRANSFER_CSV_ERRORS,
    MAX_PRODUCT_CSV_ROWS as MAX_TRANSFER_CSV_ROWS,
    csv_response,
    read_product_csv,
)


TRANSFER_CSV_HEADERS = ('sku', 'quantity')


def transfer_csv_template():
    return csv_response('warehouse-transfer-template.csv', TRANSFER_CSV_HEADERS, ())


def parse_transfer_rows(upload):
    """Return the CSV line number, SKU and quantity for each product row."""
    rows = read_product_csv(upload, TRANSFER_CSV_HEADERS, document='transfer')
    return [(line, row['sku'], row['quantity']) for line, row in rows]
