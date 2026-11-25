"""Appendix C single-document line-item CSV/XLSX import for Sale and Purchase orders."""

from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from common.audit import log_audit_event
from common.io_utils import parse_import_file
from common.models import AuditLog, SettlementMode, TaxMethod
from common.money import q_money, q_qty
from inventory.models import Product, TaxRule, UnitOfMeasure, require_convertible_unit
from people.models import Party
from warehouse.models import WareHouse
from warehouse.scope import check_warehouse, lock_warehouse_scope


SALE_COLUMNS = ['product_code', 'quantity', 'sale_unit_code', 'price', 'discount_per_unit', 'tax_name']
PURCHASE_COLUMNS = ['product_code', 'quantity', 'purchase_unit_code', 'cost', 'discount_per_unit', 'tax_name']


def _parse_trade_lines(csv_content, filename: str, *, kind: str) -> dict:
    required = SALE_COLUMNS if kind == 'sale' else PURCHASE_COLUMNS
    unit_col = 'sale_unit_code' if kind == 'sale' else 'purchase_unit_code'
    price_col = 'price' if kind == 'sale' else 'cost'

    headers, rows = parse_import_file(csv_content, filename=filename)
    missing = [col for col in required if col not in headers]
    if missing:
        raise ValidationError({'file': f'Missing required headers: {", ".join(missing)}.'})
    if not rows:
        raise ValidationError({'file': 'Add at least one line item row to import.'})

    parsed = []
    errors = []
    for row in rows:
        rnum = row['_row_number']
        sku = (row.get('product_code') or '').strip()
        product = Product.objects.select_related('unit').filter(sku=sku).first()
        if product is None:
            errors.append({'row': rnum, 'field': 'product_code', 'error': f'Unknown product SKU "{sku}".'})
            continue

        unit_code = (row.get(unit_col) or '').strip()
        if unit_code.lower() == 'n/a':
            if product.product_type_id != 'digital':
                errors.append({'row': rnum, 'field': unit_col, 'error': '"n/a" unit is only allowed for digital products.'})
                continue
            unit = product.unit
        else:
            unit = UnitOfMeasure.objects.filter(code=unit_code).first()
            if unit is None:
                errors.append({'row': rnum, 'field': unit_col, 'error': f'Unknown unit code "{unit_code}".'})
                continue

        try:
            require_convertible_unit(unit, product.unit, field=unit_col)
            qty = q_qty(row.get('quantity') or 0)
            if qty <= 0:
                raise ValueError('Quantity must be positive.')
            unit_val = q_money(row.get(price_col) or 0, digits=4)
            if unit_val < 0:
                raise ValueError(f'{price_col} cannot be negative.')
            disc_per_unit = q_money(row.get('discount_per_unit') or 0, digits=4)
            if disc_per_unit < 0:
                raise ValueError('discount_per_unit cannot be negative.')
            line_discount = q_money(disc_per_unit * qty, digits=4)
            if line_discount > q_money(qty * unit_val, digits=4):
                raise ValueError('Line discount exceeds gross line amount.')
        except Exception as exc:
            errors.append({'row': rnum, 'field': 'row', 'error': str(exc)})
            continue

        tax_name = (row.get('tax_name') or '').strip()
        tax_rate = Decimal('0')
        if tax_name and tax_name.lower() not in ('none', 'no tax', '0', 'exempt'):
            rule = TaxRule.objects.filter(name__iexact=tax_name, is_active=True).first()
            if rule is None:
                errors.append({'row': rnum, 'field': 'tax_name', 'error': f'Unknown tax rule "{tax_name}".'})
                continue
            tax_rate = rule.rate

        parsed.append({
            'row_number': rnum,
            'product': product,
            'unit': unit,
            'quantity': qty,
            'unit_price': unit_val,
            'discount_amount': line_discount,
            'tax_rate': tax_rate,
            'tax_method': getattr(product, 'tax_method', TaxMethod.EXCLUSIVE) or TaxMethod.EXCLUSIVE,
        })

    return {'headers': headers, 'parsed': parsed, 'errors': errors}


@transaction.atomic
def import_sale_csv(
    actor,
    *,
    warehouse_id: int,
    csv_content,
    filename: str = 'sale.csv',
    customer_id: int | None = None,
    order_date=None,
    settlement_mode: str = SettlementMode.DEFERRED,
    notes: str = '',
    preview: bool = False,
):
    from sale.models import SalesOrder, SalesOrderItem

    if not has_role_permissions(actor, ('sale.add_salesorder', 'sale.add_salesorderitem')):
        raise PermissionDenied('You do not have permission to import sales orders.')
    scope = lock_warehouse_scope(actor)
    check_warehouse(scope, int(warehouse_id))
    wh = WareHouse.objects.filter(pk=warehouse_id, is_active=True).first()
    if wh is None:
        raise ValidationError({'warehouse': 'Choose an active warehouse.'})

    customer = None
    if customer_id:
        customer = Party.objects.filter(pk=customer_id, is_customer=True, is_active=True).first()
        if customer is None:
            raise ValidationError({'customer': 'Choose an active customer.'})

    res = _parse_trade_lines(csv_content, filename, kind='sale')
    if res['errors']:
        raise ValidationError({'rows': res['errors']})

    if preview:
        return {
            'preview': True,
            'columns': SALE_COLUMNS,
            'line_count': len(res['parsed']),
            'lines': [
                {
                    'sku': row['product'].sku,
                    'unit': row['unit'].code,
                    'quantity': str(row['quantity']),
                    'unit_price': str(row['unit_price']),
                    'discount_amount': str(row['discount_amount']),
                    'tax_rate': str(row['tax_rate']),
                }
                for row in res['parsed']
            ],
        }

    username = actor.get_username()
    order = SalesOrder.objects.create(
        customer=customer,
        warehouse=wh,
        order_date=order_date or timezone.localdate(),
        currency_id=settings.INVENTORY_BASE_CURRENCY,
        exchange_rate=Decimal('1'),
        settlement_mode=settlement_mode or SettlementMode.DEFERRED,
        notes=notes or '',
        create_by=username,
        modified_by=username,
    )
    for idx, line in enumerate(res['parsed'], start=1):
        SalesOrderItem.objects.create(
            sales_order=order,
            line_number=idx,
            product=line['product'],
            unit=line['unit'],
            quantity=line['quantity'],
            unit_price=line['unit_price'],
            discount_amount=line['discount_amount'],
            tax_rate=line['tax_rate'],
            tax_method=line['tax_method'],
            create_by=username,
            modified_by=username,
        )
    log_audit_event(
        actor, AuditLog.Action.IMPORT, instance=order,
        changes={'lines': len(res['parsed'])}, module=AuditLog.Module.SALE, warehouse=wh,
    )
    return order


@transaction.atomic
def import_purchase_csv(
    actor,
    *,
    warehouse_id: int,
    supplier_id: int,
    csv_content,
    filename: str = 'purchase.csv',
    order_date=None,
    settlement_mode: str = SettlementMode.DEFERRED,
    notes: str = '',
    preview: bool = False,
):
    from purchase.models import PurchaseOrder, PurchaseOrderItem

    if not has_role_permissions(actor, ('purchase.add_purchaseorder', 'purchase.add_purchaseorderitem')):
        raise PermissionDenied('You do not have permission to import purchase orders.')
    scope = lock_warehouse_scope(actor)
    check_warehouse(scope, int(warehouse_id))
    wh = WareHouse.objects.filter(pk=warehouse_id, is_active=True).first()
    if wh is None:
        raise ValidationError({'warehouse': 'Choose an active warehouse.'})
    supplier = Party.objects.filter(pk=supplier_id, is_supplier=True, is_active=True).first()
    if supplier is None:
        raise ValidationError({'supplier': 'Choose an active supplier.'})

    res = _parse_trade_lines(csv_content, filename, kind='purchase')
    if res['errors']:
        raise ValidationError({'rows': res['errors']})

    if preview:
        return {
            'preview': True,
            'columns': PURCHASE_COLUMNS,
            'line_count': len(res['parsed']),
            'lines': [
                {
                    'sku': row['product'].sku,
                    'unit': row['unit'].code,
                    'quantity': str(row['quantity']),
                    'unit_price': str(row['unit_price']),
                    'discount_amount': str(row['discount_amount']),
                    'tax_rate': str(row['tax_rate']),
                }
                for row in res['parsed']
            ],
        }

    username = actor.get_username()
    order = PurchaseOrder.objects.create(
        supplier=supplier,
        warehouse=wh,
        order_date=order_date or timezone.localdate(),
        currency_id=settings.INVENTORY_BASE_CURRENCY,
        exchange_rate=Decimal('1'),
        settlement_mode=settlement_mode or SettlementMode.DEFERRED,
        notes=notes or '',
        create_by=username,
        modified_by=username,
    )
    for idx, line in enumerate(res['parsed'], start=1):
        PurchaseOrderItem.objects.create(
            purchase_order=order,
            line_number=idx,
            product=line['product'],
            unit=line['unit'],
            quantity=line['quantity'],
            unit_price=line['unit_price'],
            discount_amount=line['discount_amount'],
            tax_rate=line['tax_rate'],
            tax_method=line['tax_method'],
            create_by=username,
            modified_by=username,
        )
    log_audit_event(
        actor, AuditLog.Action.IMPORT, instance=order,
        changes={'lines': len(res['parsed'])}, module=AuditLog.Module.PURCHASE, warehouse=wh,
    )
    return order
