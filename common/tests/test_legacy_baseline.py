import json
from datetime import date, time
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db.models import Sum
from django.test import TestCase
from rest_framework.exceptions import NotFound

from accounting.models import AccountTransaction, FinancialAccount
from accounting.services import accounts_with_balances
from hrm.models import (
    Employee, EmployeeCompensation, EmployeeScheduleAssignment, SalaryComponent,
    SalaryStructure, SalaryStructureLine, WorkSchedule,
)
from hrm.payroll import preview_payroll, save_payroll
from inventory.models import Currency, Product, UnitOfMeasure
from people.models import Party
from purchase.models import PurchaseOrder, PurchaseOrderItem, PurchaseReturn, PurchaseReturnItem
from purchase.returns import complete_return as complete_purchase_return
from purchase.services import receive_purchase
from sale.models import SalesOrder, SalesOrderItem, SalesReturn, SalesReturnItem
from sale.returns import complete_return as complete_sale_return
from sale.services import complete_sale
from warehouse.models import Stock, WareHouse
from warehouse.scope import scoped_queryset, warehouse_scope
from warehouse.tests.helpers import assign_warehouse_role


FIXTURE_PATH = Path(__file__).resolve().parent / 'fixtures' / 'legacy_baseline.json'


class LegacyBaselineRegressionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.expected = json.loads(FIXTURE_PATH.read_text(encoding='utf-8'))
        cls.admin = get_user_model().objects.create_superuser(username='golden-admin')
        cls.branch_user = get_user_model().objects.create_user(username='golden-branch-user')
        cls.wh_main = WareHouse.objects.create(name='Golden Main', address='Dhaka', phone='01700')
        cls.wh_branch = WareHouse.objects.create(name='Golden Branch', address='Chattogram', phone='01800')
        cls.role = assign_warehouse_role(cls.branch_user, warehouse=cls.wh_main).role
        cls.role.permissions.add(
            Permission.objects.get(content_type__app_label='sale', codename='view_salesorder'),
            Permission.objects.get(content_type__app_label='sale', codename='change_salesorder'),
        )
        cls.unit = UnitOfMeasure.objects.create(name='Pieces', code='GOLD-PCS')
        cls.usd, _ = Currency.objects.get_or_create(
            code='USD', defaults={'name': 'US Dollar', 'symbol': '$', 'exchange_rate': Decimal('120')},
        )
        cls.product = Product.objects.create(
            name='Golden Item', sku='GOLD-SKU', unit=cls.unit, currency_id='BDT',
            cost_price=Decimal('50.0000'), sale_price=Decimal('100.0000'),
        )
        cls.customer = Party.objects.create(code='GOLD-CUST', name='Golden Customer', is_customer=True)
        cls.supplier = Party.objects.create(code='GOLD-SUP', name='Golden Supplier', is_supplier=True)
        cls.cash = FinancialAccount.objects.create(
            name='Golden Cash', type=FinancialAccount.Type.CASH, warehouse=cls.wh_main,
            opening_balance=Decimal('1000.0000'),
        )
        Stock.objects.create(warehouse=cls.wh_main, product=cls.product, quantity=Decimal('10'))

    def _balance(self, account_id):
        return accounts_with_balances(FinancialAccount.objects.filter(pk=account_id)).get().balance

    def test_legacy_sale_purchase_returns_and_mixed_currency(self):
        # 1. Legacy sale complete (auto payment)
        sale = SalesOrder.objects.create(
            customer=self.customer, warehouse=self.wh_main, payment_account=self.cash,
            currency_id='BDT', exchange_rate=Decimal('1'), shipping_cost=Decimal('10.0000'),
        )
        sale_line = SalesOrderItem.objects.create(
            sales_order=sale, line_number=1, product=self.product, unit=self.unit,
            quantity=Decimal('3'), unit_price=Decimal('100.0000'),
            discount_amount=Decimal('30.0000'), tax_rate=Decimal('10.0000'),
        )
        sale, completed = complete_sale(self.admin, sale.pk)
        self.assertTrue(completed)
        exp_sale = self.expected['sale_auto']
        self.assertEqual(f'{sale.subtotal:.4f}', exp_sale['subtotal'])
        self.assertEqual(f'{sale.tax_total:.4f}', exp_sale['tax_total'])
        self.assertEqual(f'{sale.grand_total:.4f}', exp_sale['grand_total'])
        self.assertEqual(
            f'{Stock.objects.get(warehouse=self.wh_main, product=self.product).quantity:.6f}',
            exp_sale['stock_after'],
        )
        self.assertEqual(f'{self._balance(self.cash.pk):.4f}', exp_sale['account_balance_after'])

        # 2. Legacy sale return refund (1 unit -> 99.0000 = (270 + 27) / 3)
        s_ret = SalesReturn.objects.create(sales_order=sale)
        SalesReturnItem.objects.create(sales_return=s_ret, sales_order_item=sale_line, quantity=Decimal('1'))
        s_ret, s_ret_done = complete_sale_return(self.admin, s_ret.pk)
        self.assertTrue(s_ret_done)
        exp_sret = self.expected['sale_return']
        self.assertEqual(
            f'{s_ret.refund_payments.aggregate(total=Sum("amount"))["total"]:.4f}',
            exp_sret['refund_amount'],
        )
        self.assertEqual(
            f'{Stock.objects.get(warehouse=self.wh_main, product=self.product).quantity:.6f}',
            exp_sret['stock_after'],
        )
        self.assertEqual(f'{self._balance(self.cash.pk):.4f}', exp_sret['account_balance_after'])

        # 3. Legacy purchase receive (auto payment)
        po = PurchaseOrder.objects.create(
            supplier=self.supplier, warehouse=self.wh_main, payment_account=self.cash,
            currency_id='BDT', exchange_rate=Decimal('1'), shipping_cost=Decimal('15.0000'),
        )
        po_line = PurchaseOrderItem.objects.create(
            purchase_order=po, line_number=1, product=self.product, unit=self.unit,
            quantity=Decimal('5'), unit_price=Decimal('100.0000'), tax_rate=Decimal('5.0000'),
        )
        po, received = receive_purchase(self.admin, po.pk)
        self.assertTrue(received)
        exp_po = self.expected['purchase_auto']
        self.assertEqual(f'{po.subtotal:.4f}', exp_po['subtotal'])
        self.assertEqual(f'{po.tax_total:.4f}', exp_po['tax_total'])
        self.assertEqual(f'{po.grand_total:.4f}', exp_po['grand_total'])
        self.assertEqual(
            f'{Stock.objects.get(warehouse=self.wh_main, product=self.product).quantity:.6f}',
            exp_po['stock_after'],
        )
        self.assertEqual(f'{self._balance(self.cash.pk):.4f}', exp_po['account_balance_after'])

        # 4. Legacy purchase return refund (2 units -> 210.0000)
        p_ret = PurchaseReturn.objects.create(purchase_order=po)
        PurchaseReturnItem.objects.create(purchase_return=p_ret, purchase_order_item=po_line, quantity=Decimal('2'))
        p_ret, p_ret_done = complete_purchase_return(self.admin, p_ret.pk)
        self.assertTrue(p_ret_done)
        exp_pret = self.expected['purchase_return']
        self.assertEqual(f'{p_ret.refund_payments.aggregate(total=Sum("amount"))["total"]:.4f}', exp_pret['refund_amount'])
        self.assertEqual(
            f'{Stock.objects.get(warehouse=self.wh_main, product=self.product).quantity:.6f}',
            exp_pret['stock_after'],
        )
        self.assertEqual(f'{self._balance(self.cash.pk):.4f}', exp_pret['account_balance_after'])

        # 5. Mixed-currency order (USD @ 120 -> 10 USD = 1200 BDT)
        fx_sale = SalesOrder.objects.create(
            customer=self.customer, warehouse=self.wh_main, payment_account=self.cash,
            currency_id='USD', exchange_rate=Decimal('120.00000000'),
        )
        SalesOrderItem.objects.create(
            sales_order=fx_sale, line_number=1, product=self.product, unit=self.unit,
            quantity=Decimal('1'), unit_price=Decimal('10.0000'),
        )
        fx_sale, fx_done = complete_sale(self.admin, fx_sale.pk)
        self.assertTrue(fx_done)
        exp_fx = self.expected['mixed_currency_sale']
        self.assertEqual(f'{fx_sale.grand_total:.4f}', exp_fx['grand_total_order_ccy'])
        self.assertEqual(f'{fx_sale.payments.get().amount:.4f}', exp_fx['payment_base_ccy'])

        # Verify AccountTransaction net sum equals current cash balance minus opening
        tx_in = AccountTransaction.objects.filter(
            account=self.cash, direction=AccountTransaction.Direction.INFLOW,
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        tx_out = AccountTransaction.objects.filter(
            account=self.cash, direction=AccountTransaction.Direction.OUTFLOW,
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        self.assertEqual(self.cash.opening_balance + tx_in - tx_out, self._balance(self.cash.pk))

    def test_branch_scoped_user_isolation(self):
        foreign_sale = SalesOrder.objects.create(
            customer=self.customer, warehouse=self.wh_branch, payment_account=None,
            currency_id='BDT', exchange_rate=Decimal('1'),
        )
        local_sale = SalesOrder.objects.create(
            customer=self.customer, warehouse=self.wh_main, payment_account=self.cash,
            currency_id='BDT', exchange_rate=Decimal('1'),
        )
        scope = warehouse_scope(self.branch_user)
        visible_ids = list(scoped_queryset(SalesOrder.objects.all(), scope).values_list('pk', flat=True))
        self.assertEqual(visible_ids, [local_sale.pk])
        with self.assertRaises(NotFound):
            complete_sale(self.branch_user, foreign_sale.pk)

    def test_golden_payroll_run(self):
        emp = Employee.objects.create(employee_code='GOLD-EMP', full_name='Golden Staff', joining_date='2026-01-01')
        structure = SalaryStructure.objects.create(code='gold-monthly', name='Golden Monthly', currency_id='BDT')
        basic = SalaryComponent.objects.create(code='gold-basic', name='Basic', type='earning')
        allowance = SalaryComponent.objects.create(code='gold-allow', name='Allowance', type='earning')
        SalaryStructureLine.objects.create(
            salary_structure=structure, component=basic,
            calculation_method=SalaryStructureLine.CalculationMethod.BASIC_SALARY,
        )
        SalaryStructureLine.objects.create(
            salary_structure=structure, component=allowance,
            calculation_method=SalaryStructureLine.CalculationMethod.PERCENTAGE_OF_BASIC,
            percentage=Decimal('50'),
        )
        EmployeeCompensation.objects.create(
            employee=emp, salary_structure=structure,
            basic_salary=Decimal('30000.0000'), effective_from='2026-01-01',
        )
        schedule = WorkSchedule.objects.create(
            code='gold-sched', name='Golden Schedule', start_time=time(9), end_time=time(17),
            working_days=[0, 1, 2, 3, 4],
        )
        EmployeeScheduleAssignment.objects.create(employee=emp, schedule=schedule, effective_from='2026-01-01')

        preview = preview_payroll(date(2026, 11, 1), 'BDT')
        self.assertTrue(preview['can_calculate'])
        run, created = save_payroll(date(2026, 11, 1), 'BDT', preview['preview_token'], self.admin)
        self.assertTrue(created)
        slip = run.payslips.get(employee=emp)
        exp_pay = self.expected['payroll_run']
        self.assertEqual(f'{slip.gross_pay:.4f}', exp_pay['gross_pay'])
        self.assertEqual(f'{slip.total_deductions:.4f}', exp_pay['total_deductions'])
        self.assertEqual(f'{slip.net_pay:.4f}', exp_pay['net_pay'])
