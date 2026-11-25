from datetime import date
from decimal import Decimal
from io import BytesIO
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from pypdf import PdfReader
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.test import APIClient

from accounting.models import FinancialAccount
from common.models import GeneralSetting, SettlementMode, TaxMethod
from common.settings_store import get_general
from inventory.models import (
    Product, ProductComboItem, ProductType, TaxRule, UnitOfMeasure, WarehouseProductPrice,
)
from inventory.pricing import resolve_unit_price
from people.models import CustomerGroup, Party
from purchase.models import PurchaseOrder, PurchaseOrderItem
from purchase.services import receive_purchase
from sale.models import SalesOrder, SalesOrderItem, SalesQuotation, SalesQuotationItem, SalesReturn, SalesReturnItem
from sale.returns import complete_return as complete_sale_return
from sale.services import complete_sale
from warehouse.models import Stock, StockBatch, StockMovement, StockSerial, WareHouse
from warehouse.opening import post_opening_stock


class Inv02Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(username='inv02-admin', password='pw')
        ProductType.objects.get_or_create(code='service', defaults={'name': 'Service'})
        ProductType.objects.get_or_create(code='combo', defaults={'name': 'Combo'})
        cls.wh = WareHouse.objects.create(name='INV02 WH', address='Dhaka', phone='01')
        cls.cash = FinancialAccount.objects.create(
            name='INV02 Cash', type=FinancialAccount.Type.CASH, warehouse=cls.wh, opening_balance=5000,
        )
        cls.pcs = UnitOfMeasure.objects.create(name='Piece', code='INV2-PCS')
        cls.box = UnitOfMeasure.objects.create(
            name='Box', code='INV2-BOX', base_unit=cls.pcs,
            operator=UnitOfMeasure.Operator.MULTIPLY, operation_value=Decimal('12'),
        )
        cls.kg = UnitOfMeasure.objects.create(name='Kilogram', code='INV2-KG')

        cls.widget = Product.objects.create(
            sku='INV2-WIDGET', name='Widget', unit=cls.pcs, purchase_unit=cls.box, sale_unit=cls.pcs,
            currency_id='BDT', cost_price=Decimal('10'), sale_price=Decimal('20'),
            barcode='INV2-WIDGET-BAR', barcode_symbology='code128',
        )
        cls.service = Product.objects.create(
            sku='INV2-SVC', name='Install Service', unit=cls.pcs,
            product_type_id='service', currency_id='BDT', cost_price=0, sale_price=100,
        )
        cls.combo = Product.objects.create(
            sku='INV2-COMBO', name='Widget Combo', unit=cls.pcs,
            product_type_id='combo', currency_id='BDT', cost_price=0, sale_price=50,
        )
        ProductComboItem.objects.create(combo_product=cls.combo, component_product=cls.widget, quantity=Decimal('2'))

    def test_f_inv_3_units_service_and_combo(self):
        # Buy 2 boxes (factor 12) -> +24 base units
        po = PurchaseOrder.objects.create(
            warehouse=self.wh, currency_id='BDT', exchange_rate=1, settlement_mode=SettlementMode.DEFERRED,
        )
        PurchaseOrderItem.objects.create(
            purchase_order=po, line_number=1, product=self.widget, unit=self.box,
            quantity=Decimal('2'), unit_price=Decimal('120'),
        )
        receive_purchase(self.admin, po.pk, settlement_mode=SettlementMode.DEFERRED)
        stock = Stock.objects.get(warehouse=self.wh, product=self.widget)
        self.assertEqual(stock.quantity, Decimal('24'))
        self.assertEqual(stock.average_cost, Decimal('10'))

        # Incompatible unit rejected
        bad_line = SalesOrderItem(
            sales_order=SalesOrder.objects.create(warehouse=self.wh, currency_id='BDT', exchange_rate=1),
            line_number=1, product=self.widget, unit=self.kg, quantity=Decimal('1'), unit_price=Decimal('20'),
        )
        with self.assertRaises(DjangoValidationError):
            bad_line.full_clean()

        # Sell 3 pieces + 1 service -> widget stock = 21, service creates zero movement
        so = SalesOrder.objects.create(
            warehouse=self.wh, currency_id='BDT', exchange_rate=1, settlement_mode=SettlementMode.DEFERRED,
        )
        SalesOrderItem.objects.create(
            sales_order=so, line_number=1, product=self.widget, unit=self.pcs, quantity=Decimal('3'), unit_price=20,
        )
        SalesOrderItem.objects.create(
            sales_order=so, line_number=2, product=self.service, unit=self.pcs, quantity=Decimal('1'), unit_price=100,
        )
        complete_sale(self.admin, so.pk, settlement_mode=SettlementMode.DEFERRED)
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, Decimal('21'))
        self.assertFalse(StockMovement.objects.filter(product=self.service).exists())

        # Sell 2 combos (each issues 2 widgets = 4 widgets) -> widget stock = 17
        so_combo = SalesOrder.objects.create(
            warehouse=self.wh, currency_id='BDT', exchange_rate=1, settlement_mode=SettlementMode.DEFERRED,
        )
        combo_line = SalesOrderItem.objects.create(
            sales_order=so_combo, line_number=1, product=self.combo, unit=self.pcs, quantity=Decimal('2'), unit_price=50,
        )
        complete_sale(self.admin, so_combo.pk, settlement_mode=SettlementMode.DEFERRED)
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, Decimal('17'))
        combo_line.refresh_from_db()
        self.assertEqual(combo_line.unit_cost_base, Decimal('20'))  # 2 * 10 per combo

    def test_combo_cannot_contain_itself_or_other_combo(self):
        with self.assertRaises(DjangoValidationError):
            ProductComboItem(combo_product=self.combo, component_product=self.combo, quantity=1).full_clean()
        combo2 = Product.objects.create(
            sku='INV2-COMBO2', name='Nested Combo', unit=self.pcs, product_type_id='combo', currency_id='BDT',
        )
        with self.assertRaises(DjangoValidationError):
            ProductComboItem(combo_product=self.combo, component_product=combo2, quantity=1).full_clean()

    def test_batch_serial_tracking_expiry_and_atomic_rollback(self):
        tracked = Product.objects.create(
            sku='INV2-TRK', name='Phone', unit=self.pcs, currency_id='BDT',
            cost_price=500, sale_price=700, has_batch_expiry=True, has_imei_serial=True,
        )
        po = PurchaseOrder.objects.create(
            warehouse=self.wh, currency_id='BDT', exchange_rate=1, settlement_mode=SettlementMode.DEFERRED,
        )
        po_line = PurchaseOrderItem.objects.create(
            purchase_order=po, line_number=1, product=tracked, unit=self.pcs, quantity=2, unit_price=500,
        )
        receive_purchase(
            self.admin, po.pk,
            receipts=[{
                'order_line': po_line.pk, 'quantity': '2',
                'batch_number': 'LOT-FEB', 'expiry_date': date(2026, 2, 15),
                'serials': ['IMEI-001', 'IMEI-002'],
            }],
            receipt_date=date(2026, 1, 10),
            request_key=uuid4(),
            settlement_mode=SettlementMode.DEFERRED,
        )
        lot = StockBatch.objects.get(warehouse=self.wh, product=tracked, batch_number='LOT-FEB')
        self.assertEqual(lot.quantity, Decimal('2'))
        self.assertEqual(StockBatch.objects.filter(warehouse=self.wh, product=tracked).aggregate(s=Sum('quantity'))['s'], Decimal('2'))

        # Filter Feb-expiring lot via API
        client = APIClient()
        client.force_login(self.admin)
        b_resp = client.get(reverse('warehouse:batch-list'), {'expiry_before': '2026-02-28', 'expiry_after': '2026-02-01'})
        self.assertEqual(b_resp.status_code, 200)
        self.assertEqual([r['batch_number'] for r in b_resp.data['results']], ['LOT-FEB'])

        # Expired batch rejected when block_expired_batch_sale=True
        so_exp = SalesOrder.objects.create(
            warehouse=self.wh, currency_id='BDT', exchange_rate=1,
            order_date=date(2026, 3, 1), settlement_mode=SettlementMode.DEFERRED,
        )
        SalesOrderItem.objects.create(
            sales_order=so_exp, line_number=1, product=tracked, unit=self.pcs,
            quantity=1, unit_price=700, batch=lot, serial_numbers=['IMEI-001'],
        )
        with self.assertRaises(DRFValidationError):
            complete_sale(self.admin, so_exp.pk, settlement_mode=SettlementMode.DEFERRED)

        # Valid sale before expiry
        so_ok = SalesOrder.objects.create(
            warehouse=self.wh, currency_id='BDT', exchange_rate=1,
            order_date=date(2026, 1, 20), settlement_mode=SettlementMode.DEFERRED,
        )
        so_line = SalesOrderItem.objects.create(
            sales_order=so_ok, line_number=1, product=tracked, unit=self.pcs,
            quantity=1, unit_price=700, batch=lot, serial_numbers=['IMEI-001'],
        )
        complete_sale(self.admin, so_ok.pk, settlement_mode=SettlementMode.DEFERRED)
        lot.refresh_from_db()
        self.assertEqual(lot.quantity, Decimal('1'))
        self.assertEqual(StockSerial.objects.get(serial_number='IMEI-001').status, StockSerial.Status.SOLD)

        # Selling same serial twice rejected, and blocks an accompanying line in same order atomically!
        post_opening_stock(self.admin, [{'warehouse': self.wh.pk, 'product': self.widget.pk, 'quantity': '5'}], uuid4())
        before_widget = Stock.objects.get(warehouse=self.wh, product=self.widget).quantity

        so_dup = SalesOrder.objects.create(
            warehouse=self.wh, currency_id='BDT', exchange_rate=1,
            order_date=date(2026, 1, 21), settlement_mode=SettlementMode.DEFERRED,
        )
        SalesOrderItem.objects.create(
            sales_order=so_dup, line_number=1, product=self.widget, unit=self.pcs, quantity=1, unit_price=20,
        )
        SalesOrderItem.objects.create(
            sales_order=so_dup, line_number=2, product=tracked, unit=self.pcs,
            quantity=1, unit_price=700, batch=lot, serial_numbers=['IMEI-001'],
        )
        with self.assertRaises(DRFValidationError):
            complete_sale(self.admin, so_dup.pk, settlement_mode=SettlementMode.DEFERRED)
        self.assertEqual(Stock.objects.get(warehouse=self.wh, product=self.widget).quantity, before_widget)

        # Sale return restores original batch & serial to RETURNED
        sr = SalesReturn.objects.create(sales_order=so_ok, return_date=date(2026, 1, 22))
        SalesReturnItem.objects.create(sales_return=sr, sales_order_item=so_line, quantity=Decimal('1'))
        complete_sale_return(self.admin, sr.pk)
        lot.refresh_from_db()
        self.assertEqual(lot.quantity, Decimal('2'))
        self.assertEqual(StockSerial.objects.get(serial_number='IMEI-001').status, StockSerial.Status.RETURNED)

    def test_pricing_promo_fallback_and_tax_inclusive_consistency(self):
        cg = CustomerGroup.objects.create(name='VIP INV02', percentage=Decimal('10'))
        cust = Party.objects.create(name='VIP', code='INV2-VIP', is_customer=True, customer_group=cg)
        tax_rule = TaxRule.objects.create(name='VAT 15', rate=Decimal('15'))
        p = Product.objects.create(
            sku='INV2-PRC', name='Promo Item', unit=self.pcs, currency_id='BDT',
            sale_price=Decimal('100'), tax_rule=tax_rule, tax_method=TaxMethod.INCLUSIVE,
            is_promo=True, promo_price=Decimal('75'),
            promo_start_date=date(2026, 1, 1), promo_end_date=date(2026, 1, 15),
        )
        WarehouseProductPrice.objects.create(
            warehouse=self.wh, product=p, sale_price=Decimal('90'),
            effective_from=date(2026, 1, 10), effective_to=date(2026, 1, 31),
        )
        # Active promo wins over warehouse price
        self.assertEqual(resolve_unit_price(p, warehouse=self.wh, customer=cust, date=date(2026, 1, 12)).price, Decimal('75.0000'))
        # Expired promo falls back to warehouse price
        self.assertEqual(resolve_unit_price(p, warehouse=self.wh, customer=cust, date=date(2026, 1, 20)).price, Decimal('90.0000'))
        # Out-of-window warehouse price falls back to customer group (10% off 100 = 90)
        self.assertEqual(resolve_unit_price(p, warehouse=self.wh, customer=cust, date=date(2026, 2, 5)).price, Decimal('90.0000'))

        # Tax-inclusive price 115 (15% VAT -> net 100, tax 15, total 115) matches across quote, order, return
        q = SalesQuotation.objects.create(
            customer=cust, warehouse=self.wh, currency_id='BDT', exchange_rate=1,
        )
        SalesQuotationItem.objects.create(
            quotation=q, line_number=1, product=p, unit=self.pcs, quantity=1,
            unit_price=Decimal('115'), tax_rate=Decimal('15'), tax_method=TaxMethod.INCLUSIVE,
        )
        so = SalesOrder.objects.create(
            customer=cust, warehouse=self.wh, currency_id='BDT', exchange_rate=1,
            settlement_mode=SettlementMode.DEFERRED,
        )
        so_line = SalesOrderItem.objects.create(
            sales_order=so, line_number=1, product=p, unit=self.pcs, quantity=1,
            unit_price=Decimal('115'), tax_rate=Decimal('15'), tax_method=TaxMethod.INCLUSIVE,
        )
        self.assertEqual((q.subtotal, q.tax_total, q.grand_total), (Decimal('100.0000'), Decimal('15.0000'), Decimal('115.0000')))
        self.assertEqual((so.subtotal, so.tax_total, so.grand_total), (Decimal('100.0000'), Decimal('15.0000'), Decimal('115.0000')))
        self.assertEqual(so_line.returnable_total, Decimal('115.0000'))

    def test_opening_stock_idempotency_and_barcode_labels(self):
        # Idempotent opening stock
        k = uuid4()
        r1, created1 = post_opening_stock(
            self.admin, [{'warehouse': self.wh.pk, 'product': self.widget.pk, 'quantity': '5', 'unit_cost_base': '10'}], k,
        )
        r2, created2 = post_opening_stock(
            self.admin, [{'warehouse': self.wh.pk, 'product': self.widget.pk, 'quantity': '5', 'unit_cost_base': '10'}], k,
        )
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(StockMovement.objects.filter(kind=StockMovement.Kind.OPENING, product=self.widget).count(), 1)

        # Barcode PDF: 2 SKUs x 3 copies = 6 labels
        ean_prod = Product.objects.create(
            sku='INV2-EAN', name='EAN Item', unit=self.pcs, currency_id='BDT',
            barcode='5901234123457', barcode_symbology='ean13', sale_price=Decimal('42.50'),
        )
        bad_ean = Product.objects.create(
            sku='INV2-BAD-EAN', name='Bad EAN', unit=self.pcs, currency_id='BDT',
            barcode='5901234123450', barcode_symbology='ean13',
        )
        client = APIClient()
        client.force_login(self.admin)
        self.assertEqual(client.get(reverse('inventory_pages:print-barcode')).status_code, 200)

        bad_pdf = client.post(
            reverse('report:barcodes-pdf'),
            {'items': [{'product': bad_ean.pk, 'copies': 1}], 'layout': 'a4_24'},
            format='json',
        )
        self.assertEqual(bad_pdf.status_code, 400)

        good_pdf = client.post(
            reverse('report:barcodes-pdf'),
            {
                'items': [{'product': self.widget.pk, 'copies': 3}, {'product': ean_prod.pk, 'copies': 3}],
                'layout': 'a4_24',
                'fields': {'product_name': {'show': True, 'size': 9}, 'price': {'show': True, 'size': 9}},
            },
            format='json',
        )
        self.assertEqual(good_pdf.status_code, 200)
        self.assertTrue(good_pdf.content.startswith(b'%PDF'))
        text = '\n'.join(page.extract_text() or '' for page in PdfReader(BytesIO(good_pdf.content)).pages)
        self.assertEqual(text.count('INV2-WIDGET-BAR'), 3)
        self.assertEqual(text.count('5901234123457'), 3)
