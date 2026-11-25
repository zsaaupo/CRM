from datetime import date, datetime, time
from decimal import Decimal
import hashlib
import hmac
import tempfile
import uuid
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from accounting.models import (
    AccountTransaction, AccountTransfer, Expense, ExpenseCategory, FinancialAccount,
    Income, IncomeCategory,
)
from common.models import (
    AuditLog, BackupSetting, CustomField, OutboxMessage, POSSetting, PaymentGatewayConfig,
)
from common.ops_services import enqueue_outbox_message, process_outbox_messages, validate_and_save_custom_fields
from inventory.models import Currency, Product, ProductCategory, ProductType, UnitOfMeasure
from people.models import Party
from purchase.models import PurchaseOrder, PurchaseOrderItem
from purchase.services import receive_purchase
from sale.models import SalesOrder, SalesOrderItem, SalesReturn, SalesReturnItem
from sale.returns import complete_sales_return
from sale.services import complete_sale
from warehouse.models import RoleWarehouseAccess, Stock, StockMovement, WareHouse
from warehouse.services import post_stock_movement


User = get_user_model()


@override_settings(TIME_ZONE='Asia/Dhaka')
class M6AcceptanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.su = User.objects.create_superuser(username='m6_su', email='m6su@example.com', password='pass')
        cls.bdt, _ = Currency.objects.get_or_create(
            code='BDT', defaults={'name': 'Taka', 'symbol': '৳', 'exchange_rate': Decimal('1')},
        )
        cls.wh_a = WareHouse.objects.create(name='Main WH', code='MAIN', address='Dhaka', phone='017')
        cls.wh_b = WareHouse.objects.create(name='Branch B', code='BRB', address='Sylhet', phone='018')

        cls.role_a = Group.objects.create(name='Branch A Viewer')
        RoleWarehouseAccess.objects.create(role=cls.role_a, all_warehouses=False, warehouse=cls.wh_a)
        cls.user_a = User.objects.create_user(username='m6_branch_a', password='pass')
        cls.user_a.groups.add(cls.role_a)
        cls.user_a.user_permissions.add(
            *Permission.objects.filter(codename__in=['view_salesorder', 'view_financialaccount', 'view_auditlog']),
        )

        cls.pcs = UnitOfMeasure.objects.create(name='Piece', code='PCS-M6')
        cls.cat = ProductCategory.objects.create(name='M6 Cat')
        cls.ptype, _ = ProductType.objects.get_or_create(code='goods', defaults={'name': 'Standard Goods'})
        cls.prod = Product.objects.create(
            name='M6 Item', sku='M6-001', product_type=cls.ptype, category=cls.cat,
            unit=cls.pcs, currency=cls.bdt, cost_price=Decimal('60.0000'), sale_price=Decimal('100.0000'),
        )
        cls.cust = Party.objects.create(code='M6-CUST', name='M6 Customer', is_customer=True, email='m6c@example.com')
        cls.sup = Party.objects.create(code='M6-SUP', name='M6 Supplier', is_supplier=True)

    def _auth(self, user=None):
        c = APIClient()
        c.force_authenticate(user=user or self.su)
        return c

    def test_f_acc_1_account_balances_transfer_balance_sheet_and_income_expense(self):
        from accounting.services import complete_income_expense
        acc1 = FinancialAccount.objects.create(
            name='Cash 1', account_number='ACC-01', type=FinancialAccount.Type.CASH,
            currency='BDT', opening_balance=Decimal('500.0000'), warehouse=self.wh_a, is_active=True,
        )
        acc2 = FinancialAccount.objects.create(
            name='Bank 2', account_number='ACC-02', type=FinancialAccount.Type.BANK,
            currency='BDT', opening_balance=Decimal('0.0000'), warehouse=self.wh_a, is_active=True,
        )
        inc_cat = IncomeCategory.objects.create(name='Other M6')
        exp_cat = ExpenseCategory.objects.create(name='OpEx M6')

        inc = Income.objects.create(
            date=date(2026, 11, 10), category=inc_cat, warehouse=self.wh_a,
            amount=Decimal('250.0000'), payment_account=acc1, note='Misc inc', reference='REF-I1',
        )
        complete_income_expense(inc, self.su, request_key=uuid.uuid4())
        exp = Expense.objects.create(
            date=date(2026, 11, 11), category=exp_cat, warehouse=self.wh_a,
            amount=Decimal('90.0000'), payment_account=acc1, note='Misc exp', reference='REF-E1',
        )
        complete_income_expense(exp, self.su, request_key=uuid.uuid4())

        client = self._auth()
        bal_res = client.get(reverse('accounting:account-balances')).data
        row1 = next(r for r in bal_res['rows'] if r['id'] == acc1.pk)
        self.assertEqual(row1['opening'], '500.0000')
        self.assertEqual(row1['credit'], '250.0000')
        self.assertEqual(row1['debit'], '90.0000')
        self.assertEqual(row1['balance'], '660.0000')
        self.assertEqual(bal_res['totals']['balance'], '660.0000')

        # Transfer 60 between two included accounts -> combined balance still 660.0000
        from accounting.services import post_transfer
        post_transfer(
            self.su, source_account=acc1.pk, destination_account=acc2.pk,
            amount=Decimal('60.0000'), date=date(2026, 11, 12), reference='TR-M6', request_key=uuid.uuid4(),
        )
        bal_after = client.get(reverse('accounting:account-balances')).data
        self.assertEqual(bal_after['totals']['balance'], '660.0000')

        ie = client.get(reverse('accounting:income-expense-report'), {'start_date': '2026-11-01', 'end_date': '2026-11-30'}).data
        self.assertEqual(ie['total_income'], '250.0000')
        self.assertEqual(ie['total_expense'], '90.0000')
        self.assertEqual(ie['net'], '160.0000')

        bs = client.get(reverse('accounting:balance-sheet')).data
        self.assertEqual(bs['label'], 'derived')
        self.assertEqual(bs['assets']['cash_and_bank'], '660.0000')

    def test_f_rpt_1_and_2_summary_split_payments_midnight_and_dashboard_reconciliation(self):
        from accounting.services import complete_income_expense, post_sales_payment
        from django.db import transaction

        acc_cash = FinancialAccount.objects.create(
            name='RPT Cash', account_number='RPT-CASH', type=FinancialAccount.Type.CASH,
            currency='BDT', opening_balance=Decimal('1000.0000'), warehouse=self.wh_a, is_active=True,
        )
        acc_bank = FinancialAccount.objects.create(
            name='RPT Bank', account_number='RPT-BANK', type=FinancialAccount.Type.BANK,
            currency='BDT', opening_balance=Decimal('0.0000'), warehouse=self.wh_a, is_active=True,
        )

        with transaction.atomic():
            post_stock_movement(
                warehouse_id=self.wh_a.pk, product_id=self.prod.pk,
                kind=StockMovement.Kind.OPENING, quantity_delta=Decimal('10'),
                unit_cost_base=Decimal('60.0000'), reference_type='opening', reference_id=self.prod.pk,
                posting_key=f'open-m6-{self.prod.pk}', actor=self.su,
            )

        # F-RPT-1: Sell 2 @100 (cost 60 each), credit (deferred), return 1 @100 restoring cost 60
        so1 = SalesOrder.objects.create(
            customer=self.cust, warehouse=self.wh_a, order_date=date(2026, 11, 15),
            currency=self.bdt, settlement_mode='deferred',
        )
        it1 = SalesOrderItem.objects.create(
            sales_order=so1, product=self.prod, quantity=Decimal('2'), unit=self.pcs, unit_price=Decimal('100.0000'),
        )
        complete_sale(so1, None, self.su, settlement_mode='deferred')
        SalesOrder.objects.filter(pk=so1.pk).update(
            completed_at=datetime(2026, 11, 15, 17, 50, tzinfo=ZoneInfo('Asia/Dhaka')),  # local date 2026-11-15
        )
        sret = SalesReturn.objects.create(sales_order=so1, return_date=date(2026, 11, 15))
        SalesReturnItem.objects.create(sales_return=sret, sales_order_item=it1, quantity=Decimal('1'))
        complete_sales_return(sret, None, self.su)

        inc_cat = IncomeCategory.objects.create(name='Other RPT')
        exp_cat = ExpenseCategory.objects.create(name='OpEx RPT')
        inc = Income.objects.create(date=date(2026, 11, 15), category=inc_cat, warehouse=self.wh_a, amount=Decimal('5'), payment_account=acc_cash)
        complete_income_expense(inc, self.su, request_key=uuid.uuid4())
        exp = Expense.objects.create(date=date(2026, 11, 15), category=exp_cat, warehouse=self.wh_a, amount=Decimal('10'), payment_account=acc_cash)
        complete_income_expense(exp, self.su, request_key=uuid.uuid4())

        client = self._auth()
        rep = client.get(
            reverse('report:analytics-api', args=['summary']),
            {'date_from': '2026-11-15', 'date_to': '2026-11-15', 'warehouse_id': self.wh_a.pk},
        ).data['totals']
        self.assertEqual(rep['net_sales'], '100.0000')
        self.assertEqual(rep['net_cogs'], '60.0000')
        self.assertEqual(rep['gross_margin'], '40.0000')
        self.assertEqual(rep['operating_result'], '35.0000')
        self.assertNotIn('profit_ref_compat', rep)

        # F-RPT-2: 2-line sale + 3 payments counts sale amount ONCE, dashboard == report
        so2 = SalesOrder.objects.create(
            customer=self.cust, warehouse=self.wh_a, order_date=date(2026, 11, 16),
            currency=self.bdt, settlement_mode='explicit',
        )
        SalesOrderItem.objects.create(sales_order=so2, product=self.prod, quantity=Decimal('1'), unit=self.pcs, unit_price=Decimal('100.0000'))
        SalesOrderItem.objects.create(sales_order=so2, product=self.prod, quantity=Decimal('2'), unit=self.pcs, unit_price=Decimal('100.0000'))
        complete_sale(so2, None, self.su, settlement_mode='explicit', tenders=[
            {'method': 'cash', 'account_id': acc_cash.pk, 'amount': Decimal('100')},
            {'method': 'bank_transfer', 'account_id': acc_bank.pk, 'amount': Decimal('100')},
            {'method': 'cash', 'account_id': acc_cash.pk, 'amount': Decimal('100')},
        ])
        # Local midnight test: 2026-11-16 00:05 Asia/Dhaka = 2026-11-15 18:05 UTC -> must land in 2026-11-16!
        SalesOrder.objects.filter(pk=so2.pk).update(
            completed_at=datetime(2026, 11, 16, 0, 5, tzinfo=ZoneInfo('Asia/Dhaka')),
        )

        rpt_16 = client.get(
            reverse('report:analytics-api', args=['summary']),
            {'date_from': '2026-11-16', 'date_to': '2026-11-16', 'warehouse_id': self.wh_a.pk},
        ).data['totals']
        dash_16 = client.get(
            reverse('report:dashboard-api'),
            {'start_date': '2026-11-16', 'end_date': '2026-11-16', 'warehouse_id': self.wh_a.pk},
        ).data
        self.assertEqual(rpt_16['sale_amount'], '300.0000')
        self.assertEqual(dash_16['kpis']['revenue'], rpt_16['net_sales'])
        self.assertEqual(dash_16['kpis']['sale_amount'], '300.0000')

        # Empty period yields 0
        dash_empty = client.get(
            reverse('report:dashboard-api'),
            {'start_date': '2020-01-01', 'end_date': '2020-01-02', 'warehouse_id': self.wh_a.pk},
        ).data
        self.assertEqual(dash_empty['kpis']['sale_amount'], '0.0000')

        # Exports (CSV & XLSX) & permission checks
        csv_res = client.get(reverse('report:analytics-api', args=['sale']), {'format': 'csv'})
        self.assertEqual(csv_res.status_code, 200)
        xlsx_res = client.get(reverse('report:analytics-api', args=['sale']), {'format': 'xlsx'})
        self.assertEqual(xlsx_res.status_code, 200)

        br_client = self._auth(self.user_a)
        # Branch A user cannot query Branch B -> 404
        self.assertEqual(
            br_client.get(reverse('report:analytics-api', args=['summary']), {'warehouse_id': self.wh_b.pk}).status_code,
            404,
        )
        # Lacks payroll perm -> 403
        self.assertEqual(br_client.get(reverse('report:analytics-api', args=['payroll'])).status_code, 403)

    def test_people_01b_and_ops_01_to_07(self):
        client = self._auth()
        # PEOPLE-01b: Party list computed columns + create login
        plist = client.get(reverse('people:party-list'), {'is_customer': 'true'}).data['results']
        row = next(r for r in plist if r['id'] == self.cust.pk)
        for key in ('deposited_balance', 'total_due', 'purchase_due', 'reward_points'):
            self.assertIn(key, row)

        login_res = client.post(reverse('people:party-create-login', args=[self.cust.pk]), {}, format='json')
        self.assertEqual(login_res.status_code, 201)
        self.cust.refresh_from_db()
        self.assertIsNotNone(self.cust.user_id)

        # OPS-01: Settings change audited & warehouse validated
        res = client.patch(reverse('ops_settings:general'), {'company_name': 'eManager Parity Ltd'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(AuditLog.objects.filter(module='settings', action='settings_change').exists())

        # OPS-02: Outbox idempotency by source_event_key & process
        m1, c1 = enqueue_outbox_message(channel='sms', recipient='0170000', source_event_key='sale:99:sms', payload={'text': 'hi'})
        m2, c2 = enqueue_outbox_message(channel='sms', recipient='0170000', source_event_key='sale:99:sms', payload={'text': 'hi'})
        self.assertTrue(c1)
        self.assertFalse(c2)
        self.assertEqual(m1.pk, m2.pk)
        proc = process_outbox_messages()
        self.assertEqual(proc['sent'], 1)

        # OPS-03: CustomField required only on creation
        CustomField.objects.create(entity='product', label='Origin', key='origin', field_type='text', required=True)
        with self.assertRaises(Exception):
            validate_and_save_custom_fields('product', self.prod.pk, {}, is_create=True)
        validate_and_save_custom_fields('product', self.prod.pk, {'origin': 'BD'}, is_create=True)

        # OPS-05: SSLCommerz sandbox initiate + callback signature + idempotent settle
        acc = FinancialAccount.objects.create(
            name='GW Bank', account_number='GW-01', type=FinancialAccount.Type.BANK,
            currency='BDT', opening_balance=Decimal('0'), warehouse=self.wh_a, is_active=True,
        )
        so = SalesOrder.objects.create(
            customer=self.cust, warehouse=self.wh_a, order_date=date(2026, 11, 18),
            currency=self.bdt, settlement_mode='deferred',
        )
        SalesOrderItem.objects.create(sales_order=so, product=self.prod, quantity=Decimal('1'), unit=self.pcs, unit_price=Decimal('100'))
        with self.settings(TIME_ZONE='Asia/Dhaka'):
            from django.db import transaction
            with transaction.atomic():
                post_stock_movement(
                    warehouse_id=self.wh_a.pk, product_id=self.prod.pk,
                    kind=StockMovement.Kind.OPENING, quantity_delta=Decimal('2'),
                    unit_cost_base=Decimal('60.0000'), reference_type='opening', reference_id=999,
                    posting_key='open-gw-999', actor=self.su,
                )
            complete_sale(so, None, self.su, settlement_mode='deferred')

        client.post(reverse('ops_settings:gateway-list'), {'provider': 'sslcommerz', 'enabled': True, 'mode': 'sandbox', 'secret': 's3cr3t'}, format='json')
        init = client.post(reverse('ops_settings:gateway-initiate'), {'provider': 'sslcommerz', 'order': so.pk, 'amount': '100', 'account': acc.pk}, format='json').data
        sig = hmac.new(b's3cr3t', f"{init['provider_ref']}:100.0000".encode(), hashlib.sha256).hexdigest()
        cb1 = client.post(reverse('ops_settings:gateway-callback'), {'provider_ref': init['provider_ref'], 'signature': sig}, format='json').data
        cb2 = client.post(reverse('ops_settings:gateway-callback'), {'provider_ref': init['provider_ref'], 'signature': sig}, format='json').data
        self.assertFalse(cb1['idempotent_replay'])
        self.assertTrue(cb2['idempotent_replay'])
        acc.refresh_from_db()
        self.assertEqual(acc.transactions.count(), 1)

        # OPS-06: Audit log scoping (Branch A user cannot see Branch B audit rows)
        AuditLog.objects.create(module='sale', action='create', model_name='SalesOrder', object_id='1', warehouse=self.wh_b)
        br_client = self._auth(self.user_a)
        logs = br_client.get(reverse('ops_settings:audit-log-list')).data['results']
        self.assertFalse(any(l['warehouse'] == self.wh_b.pk for l in logs))

        # OPS-07: Backup blocked while feature flag off; succeeds for superuser when enabled
        self.assertEqual(client.post(reverse('ops_settings:backup'), {}, format='json').status_code, 409)
        with tempfile.TemporaryDirectory() as tmpdir:
            BackupSetting.objects.update_or_create(pk=1, defaults={'enabled': True, 'backup_dir': tmpdir})
            bk = client.post(reverse('ops_settings:backup'), {}, format='json')
            self.assertEqual(bk.status_code, 201)
