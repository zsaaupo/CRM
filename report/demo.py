"""Synthetic layout fixtures for previews; these never create database records."""
from datetime import date
from decimal import Decimal

from common.models import TradeStatus
from inventory.models import Currency, Product, UnitOfMeasure
from people.models import Party
from purchase.models import PurchaseOrder, PurchaseOrderItem
from sale.models import SalesOrder, SalesOrderItem
from warehouse.models import WareHouse, WarehouseTransfer, WarehouseTransferItem
from .services import document_context


def demo_document(kind, line_count=None):
    source = WareHouse(pk=1, name='Dhaka central warehouse', code='DHK-01', address='Example distribution centre\nDhaka, Bangladesh', phone='+880 1700 000000')
    destination = WareHouse(pk=2, name='Uttara branch', code='UTT-02', address='Example branch address\nUttara, Dhaka', phone='+880 1800 000000')
    party = Party(pk=1, code='EXAMPLE-001', name='Example Trading Company', is_customer=True, is_supplier=True,
        address='Example business address', city='Dhaka', postal_code='1205', country='Bangladesh', email='office@example.com', phone='+880 1900 000000')
    currency = Currency(code='BDT', name='Bangladeshi taka', exchange_rate=Decimal('1'))
    status = TradeStatus(code='confirmed', name='Confirmed')
    audit = {'create_by': 'Workspace administrator', 'modified_by': 'Workspace administrator'}
    if kind == 'transfer':
        record = WarehouseTransfer(pk=1, transfer_number='WT-2026-0001', source_warehouse=source,
            destination_warehouse=destination, transfer_date=date(2026, 11, 17), is_completed=True, **audit)
    else:
        kwargs = {'pk': 1, 'currency': currency, 'exchange_rate': Decimal('1'), 'warehouse': source, 'status': status,
                  'order_date': date(2026, 11, 17), 'shipping_cost': Decimal('25'), **audit}
        record = PurchaseOrder(order_number='PO-2026-0001', supplier=party, expected_date=date(2026, 11, 20),
            supplier_reference='SUP-EXAMPLE-014', is_received=False, **kwargs) if kind == 'purchase' else SalesOrder(
            order_number='SO-2026-0001', customer=party, expected_delivery_date=date(2026, 11, 20),
            customer_reference='CUST-EXAMPLE-008', billing_address='Example billing address, Dhaka',
            shipping_address='Example delivery address, Uttara, Dhaka', is_completed=True, **kwargs)
    record.notes = 'Sample document for layout review. Product quantities, prices and parties are illustrative.'
    names = ['Premium rice - চাল', 'Archive storage box', 'Office stationery set', 'Industrial packing material', 'Reusable delivery container']
    items = []
    count = line_count if line_count is not None else {'purchase': 3, 'sale': 34, 'transfer': 5}[kind]
    for index in range(count):
        unit = UnitOfMeasure(pk=1 if index % 2 == 0 else 2, name='Kilograms' if index % 2 == 0 else 'Pieces', code='KG' if index % 2 == 0 else 'PCS')
        product = Product(pk=index + 1, sku=f'ITEM-{index + 1:03}', name=names[index % len(names)], unit=unit, currency=currency)
        kwargs = {'line_number': index + 1, 'product': product, 'unit': unit,
                  'quantity': Decimal('2.500001') if index % 2 == 0 else Decimal('3'), **audit}
        if kind == 'transfer':
            item = WarehouseTransferItem(transfer=record, **kwargs)
        else:
            kwargs.update({'unit_price': Decimal('120.5000') + index, 'discount_amount': Decimal('3'),
                'tax_rate': Decimal('15'), 'description': 'Quality checked and packaged for delivery.' if index % 3 == 0 else ''})
            item = PurchaseOrderItem(purchase_order=record, **kwargs) if kind == 'purchase' else SalesOrderItem(sales_order=record, **kwargs)
        items.append(item)
    record._prefetched_objects_cache = {'items': items}
    document = document_context(kind, record)
    document['company'].update({'address': 'Example business address, Dhaka, Bangladesh', 'phone': '+880 1700 000000', 'email': 'office@example.com'})
    return document
