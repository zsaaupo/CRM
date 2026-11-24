from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from accounting.models import AccountTransfer, Expense, ExpenseCategory, FinancialAccount, Income, IncomeCategory
from purchase.models import PurchaseOrder, PurchaseReturn
from sale.models import SalesOrder, SalesReturn
from warehouse.activity import REPORTS
from warehouse.models import WareHouse, WarehouseTransfer
from warehouse.tests.helpers import assign_warehouse_role


class WarehouseReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.main = WareHouse.objects.create(name='Main')
        cls.branch = WareHouse.objects.create(name='Branch')
        cls.user = get_user_model().objects.create_user(username='report-reader')
        cls.assignment = assign_warehouse_role(cls.user, warehouse=cls.main)
        cls.expense_category = ExpenseCategory.objects.create(name='Rent')
        cls.income_category = IncomeCategory.objects.create(name='Hire')
        for warehouse in (cls.main, cls.branch):
            order_fields = dict(warehouse=warehouse, currency_id=settings.INVENTORY_BASE_CURRENCY, exchange_rate=1)
            SalesReturn.objects.create(sales_order=SalesOrder.objects.create(**order_fields))
            PurchaseReturn.objects.create(purchase_order=PurchaseOrder.objects.create(**order_fields))
            Expense.objects.create(warehouse=warehouse, category=cls.expense_category, amount='2')
            Income.objects.create(warehouse=warehouse, category=cls.income_category, amount='3')
        WarehouseTransfer.objects.create(source_warehouse=cls.main, destination_warehouse=cls.branch)
        source = FinancialAccount.objects.create(warehouse=cls.main, name='Cash', type='cash')
        destination = FinancialAccount.objects.create(warehouse=cls.branch, name='Bank', type='bank')
        for kind in ('fund_transfer', 'money_transfer'):
            AccountTransfer.objects.create(source_account=source, destination_account=destination, amount=1, transfer_type=kind)

    def setUp(self):
        self.client.force_login(self.user)
        self.page = reverse('warehouse_pages:report')

    def grant(self, *codenames):
        self.assignment.role.permissions.set(Permission.objects.filter(codename__in=codenames))

    def report(self, key, warehouse, **filters):
        return self.client.get(reverse(f'warehouse:report-{key}', args=[warehouse.pk]), filters)

    def test_warehouse_report_date_range_uses_activity_date_and_includes_both_ends(self):
        self.grant('view_income', 'view_salesreturn', 'view_warehousetransfer')
        Income.objects.filter(warehouse=self.main).delete()
        for day in ('2025-02-01', '2025-02-02', '2025-02-03', '2025-02-04'):
            Income.objects.create(warehouse=self.main, category=self.income_category, amount=1, date=day)
        for filters, expected in [
            ({'start_date': '2025-02-02', 'end_date': '2025-02-03'}, 2),
            ({'start_date': '2025-02-03'}, 2), ({'end_date': '2025-02-01'}, 1), ({}, 4),
        ]:
            self.assertEqual(self.report('income', self.main, **filters).data['count'], expected)
        day = '2025-01-07'
        SalesReturn.objects.update(return_date=day)
        WarehouseTransfer.objects.update(transfer_date=day)
        for key in ('sales-returns', 'stock-transfers'):
            self.assertEqual(self.report(key, self.main, start_date=day, end_date=day).data['count'], 1)
            self.assertEqual(self.report(key, self.main, start_date='2025-01-08').data['count'], 0)
        for filters in ({'start_date': 'bad'}, {'start_date': '2025-02-03', 'end_date': '2025-02-01'}):
            self.assertEqual(self.report('income', self.main, **filters).status_code, 400)

    def test_dedicated_warehouse_and_activity_permissions(self):
        self.assertEqual(self.client.get(self.page).status_code, 403)
        self.grant('view_salesorder', 'view_warehousetransfer')
        page = self.client.get(self.page)
        self.assertEqual(set(page.context['reports']), {'sales', 'stock-transfers'})
        self.assertFalse(page.context['choose_warehouse'])
        self.assertEqual(list(page.context['warehouses']), [self.main])
        self.assertEqual(self.report('sales', self.main).data['count'], 1)
        self.assertEqual(self.report('stock-transfers', self.main).data['count'], 1)
        self.assertEqual(self.report('sales', self.branch).status_code, 404)
        self.assertEqual(self.report('income', self.main).status_code, 403)

    def test_all_warehouse_role_switches_between_separate_reports(self):
        self.assignment.warehouse = None
        self.assignment.all_warehouses = True
        self.assignment.save()
        self.grant('view_salesorder', 'view_purchaseorder', 'view_salesreturn', 'view_purchasereturn',
                   'view_expense', 'view_income', 'view_salesquotation', 'view_warehousetransfer',
                   'view_financialaccount', 'view_accounttransfer')
        page = self.client.get(self.page)
        self.assertTrue(page.context['choose_warehouse'])
        self.assertEqual(set(page.context['reports']), set(REPORTS))
        for key in REPORTS:
            with self.subTest(key=key):
                result = self.report(key, self.branch)
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.data['count'], 0 if key == 'quotations' else 1)
                if key in ('fund-transfers', 'money-transfers'):
                    self.assertEqual(result.data['results'][0]['transfer_type'], key.replace('-transfers', '_transfer'))
        sale = self.report('sales', self.branch).data['results'][0]
        self.assertEqual(sale['warehouse'], self.branch.pk)
        self.assertEqual(self.client.post(reverse('warehouse:report-income', args=[self.main.pk])).status_code, 405)
