from datetime import date
from decimal import Decimal
import json
import uuid

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounting.models import FinancialAccount
from common.models import (
    CustomField, Language, OutboxMessage, POSSetting, RewardSetting, SMSTemplate,
)
from inventory.models import Currency, Product, ProductType, TaxRule, UnitOfMeasure
from people.models import CustomerPointLedger, Party
from purchase.models import PurchaseOrder
from report.services import document_context
from sale.loyalty import expire_reward_points, maybe_earn_points_for_order, redeem_points
from sale.models import SalesOrder, SalesOrderItem
from sale.services import complete_sale
from warehouse.models import StockMovement, WareHouse
from warehouse.services import post_stock_movement


User = get_user_model()


class ParityGapFixesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.su = User.objects.create_superuser(username='parity_su', email='psu@example.com', password='pass')
        cls.bdt, _ = Currency.objects.get_or_create(
            code='BDT', defaults={'name': 'Taka', 'symbol': '৳', 'exchange_rate': Decimal('1')},
        )
        cls.wh = WareHouse.objects.create(name='Parity WH', code='PAR', address='Dhaka', phone='01711')
        cls.pcs = UnitOfMeasure.objects.create(name='Piece', code='PCS-PAR')
        cls.goods, _ = ProductType.objects.get_or_create(code='goods', defaults={'name': 'Goods'})
        cls.digital, _ = ProductType.objects.get_or_create(code='digital', defaults={'name': 'Digital'})
        cls.tax15 = TaxRule.objects.create(name='VAT 15', rate=Decimal('15.0000'), is_active=True)

        cls.prod = Product.objects.create(
            name='Parity Widget', sku='PAR-001', product_type=cls.goods,
            unit=cls.pcs, currency=cls.bdt, cost_price=Decimal('50.0000'), sale_price=Decimal('100.0000'),
        )
        cls.digi_prod = Product.objects.create(
            name='E-Book', sku='DIG-001', product_type=cls.digital,
            unit=cls.pcs, currency=cls.bdt, cost_price=Decimal('10.0000'), sale_price=Decimal('50.0000'),
        )
        cls.cust = Party.objects.create(
            code='PAR-C', name='Parity Customer', is_customer=True, phone='01700001111', email='pc@example.com',
        )
        cls.sup = Party.objects.create(
            code='PAR-S', name='Parity Supplier', is_supplier=True, phone='01800001111',
        )
        cls.cash = FinancialAccount.objects.create(
            name='Parity Cash', account_number='PAR-CASH', type=FinancialAccount.Type.CASH,
            currency='BDT', opening_balance=Decimal('500.0000'), warehouse=cls.wh, is_active=True,
        )

    def _client(self):
        c = APIClient()
        c.force_authenticate(user=self.su)
        return c

    def test_reconcile_account_balances_command(self):
        out = call_command('reconcile_account_balances', as_json=True)
        # Re-run capturing stdout
        from io import StringIO
        buf = StringIO()
        call_command('reconcile_account_balances', as_json=True, stdout=buf)
        data = json.loads(buf.getvalue())
        self.assertEqual(data['mismatches'], [])
        self.assertGreaterEqual(data['checked'], 1)

    def test_reward_points_expiry_days_redeem_and_after_sale_sms(self):
        RewardSetting.objects.update_or_create(pk=1, defaults={
            'enable_reward_points': True,
            'sold_amount_per_point': Decimal('100.0000'),
            'minimum_sold_amount_to_get_point': Decimal('100.0000'),
            'redeem_amount_per_point': Decimal('5.0000'),
            'point_expiry_duration_days': 30,
        })
        POSSetting.objects.update_or_create(pk=1, defaults={'sms_after_sale': True})
        SMSTemplate.objects.create(code='after_sale', name='after_sale', body='Thank you!', is_active=True)

        post_stock_movement(
            warehouse_id=self.wh.pk, product_id=self.prod.pk,
            kind=StockMovement.Kind.OPENING, quantity_delta=Decimal('20'),
            unit_cost_base=Decimal('50.0000'), reference_type='opening', reference_id=self.prod.pk,
            posting_key=f'par-open-{self.prod.pk}', actor=self.su,
        )
        so = SalesOrder.objects.create(
            customer=self.cust, warehouse=self.wh, order_date=date(2026, 11, 20),
            currency=self.bdt, exchange_rate=Decimal('1'), settlement_mode='deferred',
        )
        SalesOrderItem.objects.create(
            sales_order=so, line_number=1, product=self.prod, unit=self.pcs,
            quantity=Decimal('5'), unit_price=Decimal('100.0000'),
        )
        complete_sale(self.su, so.pk, settlement_mode='deferred')

        # Earned 5 points (500 // 100) with expiry = order_date + 30 days
        ledger = CustomerPointLedger.objects.get(sales_order=so, reason=CustomerPointLedger.Reason.EARN)
        self.assertEqual(ledger.points_delta, 5)
        self.assertEqual(ledger.expires_at, date(2026, 12, 20))

        # After-sale SMS outbox created once (idempotent key)
        self.assertEqual(
            OutboxMessage.objects.filter(source_event_key=f'sale:{so.pk}:after_sale_sms').count(), 1,
        )

        # Redeem 2 points via API
        c = self._client()
        rk = str(uuid.uuid4())
        r1 = c.post(reverse('sale:loyalty-redeem'), {'customer': self.cust.pk, 'points': 2, 'request_key': rk}, format='json')
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.data['points_redeemed'], 2)
        self.assertEqual(r1.data['monetary_value'], '10.0000')
        self.assertEqual(r1.data['remaining_points'], 3)
        # Replay idempotent
        r2 = c.post(reverse('sale:loyalty-redeem'), {'customer': self.cust.pk, 'points': 2, 'request_key': rk}, format='json')
        self.assertTrue(r2.data['idempotent_replay'])

        # Expiring points clears expired balance
        expire_reward_points(as_of=date(2026, 12, 25), actor=self.su)
        self.cust.refresh_from_db()
        self.assertEqual(self.cust.reward_points, 0)

    def test_mail_sms_language_switch_and_ops_apis(self):
        c = self._client()
        r_mail = c.patch(reverse('ops_settings:mail'), {'host': 'smtp.example.com', 'port': 587, 'password': 'secret'}, format='json')
        self.assertEqual(r_mail.status_code, 200)
        self.assertTrue(r_mail.data['has_password'])
        self.assertNotIn('password', r_mail.data)

        r_sms = c.patch(reverse('ops_settings:sms'), {'provider': 'twilio', 'sender_id': 'EMGR', 'api_key': 'tok123'}, format='json')
        self.assertEqual(r_sms.status_code, 200)
        self.assertTrue(r_sms.data['has_api_key'])
        self.assertNotIn('api_key', r_sms.data)

        Language.objects.create(code='ar', name='Arabic', is_rtl=True, is_active=True)
        r_lang = c.post(reverse('ops_settings:language-switch'), {'code': 'ar'}, format='json')
        self.assertEqual(r_lang.status_code, 200)
        self.assertEqual(r_lang.data['language'], 'ar')
        self.assertTrue(r_lang.data['is_rtl'])

    def test_custom_field_serializer_and_invoice_snapshot(self):
        CustomField.objects.create(
            entity=CustomField.Entity.SALE, label='PO Ref #', key='po_ref',
            field_type='text', required=True, show_in_invoice=True, is_active=True,
        )
        c = self._client()
        # Missing required custom field blocks new sale create
        bad = c.post(reverse('sale:sales-order-list'), {
            'customer': self.cust.pk, 'warehouse': self.wh.pk, 'currency': 'BDT', 'exchange_rate': '1', 'settlement_mode': 'deferred',
        }, format='json')
        self.assertEqual(bad.status_code, 400)

        ok = c.post(reverse('sale:sales-order-list'), {
            'customer': self.cust.pk, 'warehouse': self.wh.pk, 'currency': 'BDT', 'exchange_rate': '1', 'settlement_mode': 'deferred',
            'custom_fields': {'po_ref': 'EXT-991'},
        }, format='json')
        self.assertEqual(ok.status_code, 201, ok.data)
        order_payload = ok.data['sales_order']
        self.assertEqual(order_payload['custom_fields']['po_ref'], 'EXT-991')

        so = SalesOrder.objects.get(pk=order_payload['id'])
        SalesOrderItem.objects.create(
            sales_order=so, line_number=1, product=self.digi_prod, unit=self.pcs,
            quantity=Decimal('1'), unit_price=Decimal('50.0000'),
        )
        doc = document_context('sale', so)
        self.assertIn(('PO Ref #', 'EXT-991'), doc['metadata'])

    def test_sale_and_purchase_csv_imports(self):
        c = self._client()
        sale_csv = (
            "product_code,quantity,sale_unit_code,price,discount_per_unit,tax_name\n"
            "DIG-001,2,n/a,50,5,VAT 15\n"
        )
        prev = c.post(reverse('sale:sales-order-import-csv'), {
            'warehouse': self.wh.pk, 'customer': self.cust.pk, 'csv': sale_csv, 'preview': True,
        }, format='json')
        self.assertEqual(prev.status_code, 200)
        self.assertEqual(prev.data['line_count'], 1)

        imp = c.post(reverse('sale:sales-order-import-csv'), {
            'warehouse': self.wh.pk, 'customer': self.cust.pk, 'csv': sale_csv,
        }, format='json')
        self.assertEqual(imp.status_code, 201)
        so = SalesOrder.objects.get(pk=imp.data['id'])
        item = so.items.get()
        self.assertEqual(item.discount_amount, Decimal('10.0000'))
        self.assertEqual(item.tax_rate, Decimal('15.0000'))

        # Non-digital product cannot use n/a
        bad_sale = c.post(reverse('sale:sales-order-import-csv'), {
            'warehouse': self.wh.pk,
            'csv': "product_code,quantity,sale_unit_code,price,discount_per_unit,tax_name\nPAR-001,1,n/a,100,0,none\n",
        }, format='json')
        self.assertEqual(bad_sale.status_code, 400)

        pur_csv = (
            "product_code,quantity,purchase_unit_code,cost,discount_per_unit,tax_name\n"
            "PAR-001,4,PCS-PAR,50,2,VAT 15\n"
        )
        p_imp = c.post(reverse('purchase:purchase-order-import-csv'), {
            'warehouse': self.wh.pk, 'supplier': self.sup.pk, 'csv': pur_csv,
        }, format='json')
        self.assertEqual(p_imp.status_code, 201)
        po = PurchaseOrder.objects.get(pk=p_imp.data['id'])
        self.assertEqual(po.items.get().discount_amount, Decimal('8.0000'))

    def test_dash01_payload_keys_and_m2_m6_pages_render(self):
        c = self._client()
        dash = c.get(reverse('report:dashboard-api'))
        self.assertEqual(dash.status_code, 200)
        for key in ('period', 'kpis', 'summary', 'cash_flow', 'monthly_breakdown', 'yearly', 'recent', 'best_sellers', 'best_seller_panels', 'quick_actions'):
            self.assertIn(key, dash.data)

        self.client.force_login(self.su)
        wh2 = WareHouse.objects.create(name='Parity WH2', code='PAR2', address='Sylhet', phone='01712')
        so_ui = SalesOrder.objects.create(customer=self.cust, warehouse=self.wh, currency=self.bdt, exchange_rate=Decimal('1'), settlement_mode='deferred')
        po_ui = PurchaseOrder.objects.create(supplier=self.sup, warehouse=self.wh, currency=self.bdt, exchange_rate=Decimal('1'), settlement_mode='deferred')
        from sale.models import SalesQuotation
        from warehouse.models import WarehouseTransfer
        q_ui = SalesQuotation.objects.create(customer=self.cust, warehouse=self.wh, currency=self.bdt, exchange_rate=Decimal('1'))
        tr_ui = WarehouseTransfer.objects.create(source_warehouse=self.wh, destination_warehouse=wh2)
        for url in [
            reverse('dashboard'),
            reverse('pos'),
            reverse('warehouse_pages:adjustment-list'),
            reverse('warehouse_pages:stock-count-list'),
            reverse('warehouse_pages:transfer-detail', args=[tr_ui.pk]),
            reverse('manufacturing_pages:home'),
            reverse('tasks_pages:list'),
            reverse('hrm_pages:workforce-console'),
            reverse('inventory_pages:print-barcode'),
            reverse('people_pages:customer-list'),
            reverse('people_pages:customer-detail', args=[self.cust.pk]),
            reverse('sale_pages:sale-detail', args=[so_ui.pk]),
            reverse('sale_pages:quotation-detail', args=[q_ui.pk]),
            reverse('purchase_pages:purchase-detail', args=[po_ui.pk]),
            reverse('report_pages:analytics', args=['summary']),
            reverse('common_pages:general-settings'),
            reverse('common_pages:audit-logs'),
        ]:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, f'Expected 200 for {url}')
