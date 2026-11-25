import json
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from inventory.models import Currency, Product, UnitOfMeasure
from sale.models import SalesOrder, SalesOrderItem
from warehouse.models import Stock, WareHouse
from warehouse.tests.helpers import assign_warehouse_role

from .models import MAX_AMOUNT, AccountTransaction, AccountTransfer, Expense, ExpenseCategory, ExpensePayment, FinancialAccount, SalesPayment
from .services import post_transaction


class FinanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.root = users.create_superuser(username='finance-admin')
        cls.staff = users.create_user(username='finance-staff', is_staff=True)
        cls.warehouse = WareHouse.objects.create(name='Main', address='Main address', phone='1')
        cls.other_warehouse = WareHouse.objects.create(name='Branch', address='Branch address', phone='2')
        cls.category = ExpenseCategory.objects.create(name='Transport')
        cls.account = FinancialAccount.objects.create(name='Main cash', type='cash', warehouse=cls.warehouse,
                                                      opening_balance=Decimal('100.0001'))
        cls.expense = Expense.objects.create(warehouse=cls.warehouse, category=cls.category, amount='70.0001',
                                             payment_account=cls.account)
        cls.bank = FinancialAccount.objects.create(name='Main bank', type='bank', warehouse=cls.warehouse)
        cls.shared = FinancialAccount.objects.create(name='Shared account', type='bank', opening_balance=100)
        cls.remote = FinancialAccount.objects.create(name='Branch cash', type='cash', warehouse=cls.other_warehouse,
                                                     opening_balance=100)

    def setUp(self):
        self.client = APIClient()
        self.client.force_login(self.root)
        self.payments = reverse('accounting:expense-payment-list', args=[self.expense.pk])
        self.expense_detail = reverse('accounting:expense-detail', args=[self.expense.pk])
        self.accounts = reverse('accounting:financial-account-list')
        self.transfers = reverse('accounting:account-transfer-list')

    def legacy_expense_payment(self, amount, account=None):
        account = account or self.account
        date = timezone.localdate()
        amount = Decimal(amount)
        payment = ExpensePayment.objects.create(expense=self.expense, account=account, date=date, amount=amount,
                                                method=account.type, reference='Transport receipt')
        return post_transaction(account, AccountTransaction.Direction.OUTFLOW, self.root, date=date,
                                amount=amount, expense_payment=payment)

    def complete_expense(self, expense=None):
        return self.client.post(reverse('accounting:expense-complete'),
                                {'expense_id': (expense or self.expense).pk, 'is_completed': True}, format='json')

    def complete_sale(self, order):
        return self.client.post(reverse('sale:sales-order-complete'),
                                {'sale_id': order.pk, 'is_completed': True}, format='json')

    def transfer_data(self, amount, destination=None):
        return {
            'source_account': self.account.pk, 'destination_account': (destination or self.bank).pk,
            'amount': amount, 'date': timezone.localdate().isoformat(), 'reference': 'Deposit', 'request_key': str(uuid4()),
        }

    def statement(self, account=None):
        return self.client.get(reverse('accounting:account-statement', args=[(account or self.account).pk])).data

    def sales_order(self, *, price='70.0001', **changes):
        currency = settings.INVENTORY_BASE_CURRENCY
        unit = UnitOfMeasure.objects.create(name='Sales payment unit', code='SPU')
        product = Product.objects.create(name='Sales payment item', sku='SP-ITEM', unit=unit, currency_id=currency)
        order = SalesOrder.objects.create(**{
            'currency_id': currency, 'exchange_rate': '1', 'warehouse': self.warehouse,
            'payment_account': self.account, **changes,
        })
        SalesOrderItem.objects.create(sales_order=order, line_number=1, product=product, unit=unit,
                                      quantity='1', unit_price=price)
        Stock.objects.create(warehouse=self.warehouse, product=product, quantity=5)
        return order

    def test_completing_sales_and_expenses_posts_once_to_the_same_statement(self):
        order = self.sales_order()
        payments = reverse('accounting:sales-payment-list', args=[order.pk])
        payment_page = reverse('sale_pages:sales-payment-list', args=[order.pk])
        sale_detail = reverse('sale_pages:sale-detail', args=[order.pk])
        self.assertContains(self.client.get(sale_detail), f'href="{payment_page}"')
        self.assertFalse(AccountTransaction.objects.exists())
        self.assertEqual(self.client.post(payments, {}, format='json').status_code, 400)
        self.assertEqual(self.client.post(self.payments, {}, format='json').status_code, 405)
        expense = self.complete_expense()
        sale = self.complete_sale(order)
        self.assertEqual(expense.status_code, 200, expense.data)
        self.assertEqual(sale.status_code, 200, sale.data)
        self.assertTrue(expense.data['completed_now'])
        self.assertTrue(sale.data['completed_now'])
        self.assertFalse(self.complete_expense().data['completed_now'])
        self.assertFalse(self.complete_sale(order).data['completed_now'])
        payment, = self.client.get(payments).data['results']
        self.assertEqual((payment['sales_order'], payment['amount'], payment['method']), (order.pk, '70.0001', 'cash'))
        self.assertNotContains(self.client.get(payment_page), 'Record payment')

        ledger = self.statement()
        self.assertEqual(ledger['account']['balance'], '100.0001')
        self.assertEqual([(entry['direction'], entry['amount'], entry['balance_after']) for entry in ledger['results']],
                         [('inflow', '70.0001', '100.0001'), ('outflow', '70.0001', '30.0000')])
        statement_url = reverse('accounting:account-statement', args=[self.account.pk])
        match, = self.client.get(statement_url, {'q': order.order_number}).data['results']
        self.assertEqual((match['sales_payment'], match['source'], match['reference']),
                         (payment['id'], f"Sales payment {payment['name']}", order.order_number))

    def test_foreign_currency_sale_uses_its_saved_rate_for_a_single_full_payment(self):
        currency = Currency.objects.create(code='EUR', name='Euro', exchange_rate='9')
        order = self.sales_order(price='7', currency_id=currency.code, exchange_rate='1.23456789',
                                 payment_account=self.bank)
        posted = self.complete_sale(order)
        self.assertEqual(posted.status_code, 200, posted.data)
        self.assertEqual(self.statement(self.bank)['account']['balance'], '8.6420')

    def test_failed_ledger_posting_rolls_back_expense_completion_and_sale_stock(self):
        order = self.sales_order()
        FinancialAccount.objects.filter(pk=self.account.pk).update(opening_balance=MAX_AMOUNT)
        failed = self.complete_sale(order)
        self.assertEqual(failed.status_code, 400, failed.data)
        order.refresh_from_db()
        self.assertFalse(order.is_completed)
        self.assertEqual(Stock.objects.get().quantity, Decimal('5'))
        self.assertFalse(SalesPayment.objects.exists())
        self.expense.payment_account = self.bank
        self.expense.save(update_fields=['payment_account'])
        failed = self.complete_expense()
        self.assertEqual(failed.status_code, 400, failed.data)
        self.expense.refresh_from_db()
        self.assertFalse(self.expense.is_completed)
        self.assertFalse(ExpensePayment.objects.exists())
        self.assertFalse(AccountTransaction.objects.exists())

    def test_creation_apis_require_an_eligible_payment_account_without_posting(self):
        entries = (
            ('sale:sales-order-list', {'currency': settings.INVENTORY_BASE_CURRENCY, 'exchange_rate': '1'}),
            ('accounting:expense-list', {'category': self.category.pk, 'amount': '10'}),
        )
        for route, fields in entries:
            with self.subTest(route=route):
                payload = {**fields, 'warehouse': self.warehouse.pk}
                url = reverse(route)
                for invalid in ({}, {'payment_account': self.remote.pk}):
                    bad = self.client.post(url, {**payload, **invalid}, format='json')
                    self.assertEqual(bad.status_code, 400, bad.data)
                    self.assertIn('payment_account', bad.data)
                saved = self.client.post(url, {**payload, 'payment_account': self.account.pk}, format='json')
                self.assertEqual(saved.status_code, 201, saved.data)
        self.assertFalse(AccountTransaction.objects.exists())

    def test_financial_account_creation_and_protected_opening_balance(self):
        response = self.client.post(self.accounts, {'name': ' Pay wallet ', 'type': 'mobile_banking',
                                                    'opening_balance': '42.2000'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        account = response.data['financial_account']
        self.assertEqual((account['name'], account['currency'], account['balance']),
                         ('Pay wallet', settings.INVENTORY_BASE_CURRENCY, '42.2000'))
        self.assertEqual(account['create_by'], self.root.username)
        detail = reverse('accounting:financial-account-detail', args=[account['id']])
        self.assertEqual(self.client.patch(detail, {'opening_balance': '9.0000'}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(detail, {'is_active': False}, format='json').status_code, 200)
        self.assertEqual(self.client.delete(detail).status_code, 204)

    def test_expense_completion_only_posts_the_remainder_after_a_legacy_partial_payment(self):
        before = self.client.get(self.expense_detail).data
        self.assertEqual((before['outstanding_amount'], before['payment_status']), ('70.0001', 'Unpaid'))
        partial = self.legacy_expense_payment('20.0001')
        midway = self.client.get(self.expense_detail).data
        self.assertEqual((midway['paid_amount'], midway['outstanding_amount'], midway['payment_status']),
                         ('20.0001', '50.0000', 'Partially paid'))
        response = self.complete_expense()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(ExpensePayment.objects.count(), 2)
        listing = self.client.get(reverse('accounting:expense-list')).data['results'][0]
        self.assertEqual((listing['payment_status'], listing['outstanding_amount']), ('Paid', '0.0000'))
        detail_page = self.client.get(reverse('accounting_pages:expense-detail', args=[self.expense.pk]))
        self.assertNotContains(detail_page, 'Edit expense')
        ledger = self.statement()
        self.assertEqual(ledger['account']['balance'], '30.0000')
        self.assertEqual([(entry['direction'], entry['amount'], entry['balance_after']) for entry in ledger['results']],
                         [('outflow', '50.0000', '30.0000'), ('outflow', '20.0001', '80.0000')])
        self.assertEqual(ledger['results'][1]['reference'], 'Transport receipt')
        self.assertEqual(self.client.patch(self.expense_detail, {'amount': '100'}, format='json').status_code, 409)
        self.assertEqual(self.client.delete(self.expense_detail).status_code, 409)
        self.assertEqual(self.client.delete(reverse('accounting:financial-account-detail',
                                                   args=[self.account.pk])).status_code, 409)
        payment_id = partial.expense_payment_id
        self.assertEqual(self.client.post(f'{self.payments}{payment_id}/reverse/', {}, format='json').status_code, 404)
        self.assertEqual(AccountTransaction.objects.count(), 2)

    def test_historical_expense_payment_offset_counts_toward_expense_and_account_balances(self):
        original = self.legacy_expense_payment('20.0001')
        offset = AccountTransaction.objects.create(
            account_id=original.account_id, expense_payment_id=original.expense_payment_id,
            date=original.date, direction=AccountTransaction.Direction.INFLOW, amount=original.amount,
            balance_after=original.balance_after + original.amount,
        )
        due = self.client.get(self.expense_detail).data
        self.assertEqual((due['paid_amount'], due['outstanding_amount'], due['payment_status']),
                         ('0.0000', '70.0001', 'Unpaid'))
        payment = self.client.get(self.payments).data['results'][0]
        self.assertNotIn('is_reversed', payment)
        ledger = self.statement()
        self.assertEqual(ledger['account']['balance'], '100.0001')
        entry = ledger['results'][0]
        self.assertEqual((entry['id'], entry['expense_payment']), (offset.pk, original.expense_payment_id))
        self.assertEqual(entry['direction'], 'inflow')
        self.assertNotIn('reversal_of', entry)

    def test_transfer_posting_is_atomic_and_has_no_reversal_action(self):
        response = self.client.post(self.transfers, self.transfer_data('100.0002'), format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(AccountTransfer.objects.exists())
        self.assertFalse(AccountTransaction.objects.exists())
        payload = self.transfer_data('50.3000')
        posted = self.client.post(self.transfers, payload, format='json')
        self.assertEqual(posted.status_code, 201, posted.data)
        self.assertEqual(posted.data['account_transfer']['transfer_type'], 'fund_transfer')
        self.assertEqual(self.client.post(self.transfers, payload, format='json').status_code, 200)
        self.assertEqual(self.client.get(self.transfers, {'q': 'Deposit'}).data['count'], 1)
        self.assertEqual(self.statement()['account']['balance'], '49.7001')
        self.assertEqual(self.statement(self.bank)['account']['balance'], '50.3000')
        self.assertEqual(AccountTransaction.objects.count(), 2)
        self.expense.payment_account = self.bank
        self.expense.save(update_fields=['payment_account'])
        self.assertEqual(self.complete_expense().status_code, 400)
        self.assertFalse(ExpensePayment.objects.exists())
        self.expense.amount = '10'
        self.expense.save(update_fields=['amount'])
        paid = self.complete_expense()
        self.assertEqual(paid.status_code, 200, paid.data)
        transfer_id = posted.data['account_transfer']['id']
        transfer_page = reverse('accounting_pages:account-transfer-list')
        self.assertNotContains(self.client.get(transfer_page), f'{transfer_page}0/reverse/')
        self.assertNotContains(self.client.get(transfer_page), '>Reversed<')
        self.assertEqual(self.client.get(f'{transfer_page}{transfer_id}/reverse/').status_code, 404)
        result = self.client.post(f'{self.transfers}{transfer_id}/reverse/', {}, format='json')
        self.assertEqual(result.status_code, 404)
        self.assertEqual(self.statement()['account']['balance'], '49.7001')
        self.assertEqual(self.statement(self.bank)['account']['balance'], '40.3000')
        self.assertEqual(AccountTransaction.objects.count(), 3)

    def test_transfer_type_is_validated_and_saved_by_the_api(self):
        payload = self.transfer_data('5')
        response = self.client.post(self.transfers, {**payload, 'transfer_type': 'other'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('transfer_type', response.data)

        response = self.client.post(self.transfers, {**payload, 'transfer_type': 'money_transfer'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.client.get(self.transfers).data['results'][0]['transfer_type'], 'money_transfer')
        retry = self.client.post(self.transfers, {**payload, 'transfer_type': 'fund_transfer'}, format='json')
        self.assertEqual(retry.status_code, 400)
        self.assertIn('request_key', retry.data)

    def test_transfer_list_filters_json_requests_and_transfer_pages(self):
        for transfer_type in ('fund_transfer', 'money_transfer'):
            payload = {**self.transfer_data('5'), 'transfer_type': transfer_type}
            posted = self.client.post(self.transfers, payload, format='json')
            self.assertEqual(posted.status_code, 201, posted.data)

        for label, expected in [('fund transfer', 'fund_transfer'), ('money transfer', 'money_transfer')]:
            with self.subTest(label=label):
                response = self.client.generic('GET', self.transfers, json.dumps({'transfer_type': label}),
                                               content_type='application/json')
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual([row['transfer_type'] for row in response.data['results']], [expected])

        for key, kind in [('account-transfer', 'fund_transfer'), ('money-transfer', 'money_transfer')]:
            with self.subTest(key=key):
                list_url = reverse(f'accounting_pages:{key}-list')
                add_url = reverse(f'accounting_pages:add-{key}')
                page = self.client.get(list_url)
                self.assertContains(page, f'href="{list_url}" data-nav-item class="sidebar-sub-link is-current"')
                self.assertContains(page, f'href="{add_url}"')
                api_url = page.context['list_api_url']
                self.assertContains(page, f'data-api-url="{api_url}"')
                rows = self.client.get(api_url).data['results']
                self.assertEqual([row['transfer_type'] for row in rows], [kind])
                form = self.client.get(add_url)
                self.assertContains(form, f'<input type="hidden" name="transfer_type" value="{kind}">', html=True)
                self.assertContains(form, f'action="{self.transfers}"')
                self.assertContains(form, f'data-success-url="{list_url}"')
        invalid = self.client.get(self.transfers, {'transfer_type': 'unknown'})
        self.assertEqual(invalid.status_code, 400)
        self.assertIn('transfer_type', invalid.data)

    def test_json_transfer_filter_carries_over_to_next_page(self):
        AccountTransfer.objects.bulk_create([
            AccountTransfer(source_account=self.account, destination_account=self.bank, amount=1, transfer_type=kind)
            for kind in ['money_transfer'] + ['fund_transfer'] * 21
        ])
        first = self.client.generic('GET', self.transfers, '{"transfer_type":"fund_transfer"}',
                                    content_type='application/json')
        self.assertEqual(first.data['count'], 21)
        second = self.client.get(first.data['next'])
        self.assertEqual(second.data['count'], 21)
        self.assertEqual([row['transfer_type'] for row in second.data['results']], ['fund_transfer'])

    def test_historical_transfer_offsets_remain_on_both_statements(self):
        response = self.client.post(self.transfers, self.transfer_data('50.3000'), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        transfer_id = response.data['account_transfer']['id']
        originals = AccountTransaction.objects.filter(transfer_id=transfer_id).select_related('account')
        for original in originals:
            direction = (AccountTransaction.Direction.INFLOW if original.direction == AccountTransaction.Direction.OUTFLOW
                         else AccountTransaction.Direction.OUTFLOW)
            AccountTransaction.objects.create(
                account=original.account, transfer_id=transfer_id,
                date=original.date, direction=direction, amount=original.amount, balance_after=original.account.opening_balance,
            )
        self.assertNotIn('is_reversed', self.client.get(self.transfers).data['results'][0])
        for account, direction, balance in ((self.account, 'inflow', '100.0001'), (self.bank, 'outflow', '0.0000')):
            with self.subTest(account=account):
                ledger = self.statement(account)
                self.assertEqual(ledger['account']['balance'], balance)
                entry, original = ledger['results']
                self.assertEqual((entry['direction'], entry['transfer'], original['transfer']),
                                 (direction, transfer_id, transfer_id))
                self.assertNotIn('reversal_of', entry)

    def test_warehouse_roles_scope_accounts_statements_transfers_and_payments(self):
        assignment = assign_warehouse_role(self.staff, warehouse=self.warehouse)
        assignment.role.permissions.set(Permission.objects.filter(content_type__app_label='accounting', codename__in=[
            'view_financialaccount', 'view_accounttransaction', 'view_accounttransfer', 'add_accounttransfer',
            'view_expense', 'view_expensepayment', 'change_expense',
        ]))
        self.legacy_expense_payment('5', self.shared)
        cross_transfer = self.client.post(self.transfers, self.transfer_data('3', self.shared), format='json')
        self.assertEqual(cross_transfer.status_code, 201, cross_transfer.data)
        wrong_warehouse = self.client.patch(self.expense_detail, {'payment_account': self.remote.pk}, format='json')
        self.assertEqual(wrong_warehouse.status_code, 400, wrong_warehouse.data)
        self.client.force_login(self.staff)
        self.assertEqual({row['name'] for row in self.client.get(self.accounts).data['results']},
                         {self.account.name, self.bank.name})
        self.assertEqual(self.client.get(reverse('accounting:account-statement', args=[self.shared.pk])).status_code, 404)
        self.assertEqual(self.client.get(self.transfers).data['count'], 0)
        self.assertEqual(self.client.get(self.payments).data['count'], 0)
        self.assertEqual(self.client.get(self.expense_detail).data['outstanding_amount'], '65.0001')
        denied = self.client.patch(self.expense_detail, {'payment_account': self.shared.pk}, format='json')
        self.assertEqual(denied.status_code, 400, denied.data)
        local = self.complete_expense()
        self.assertEqual(local.status_code, 200, local.data)
        self.assertEqual([row['amount'] for row in self.client.get(self.payments).data['results']], ['65.0001'])

    def test_cash_flow_lists_posted_movements_and_filters_by_account(self):
        url = reverse('accounting:cash-flow-list')
        self.assertEqual(self.client.get(url).data['count'], 0)
        self.assertEqual(self.complete_expense().status_code, 200)
        self.assertEqual(self.complete_sale(self.sales_order()).status_code, 200)
        self.assertEqual(self.client.post(self.transfers, self.transfer_data('20'), format='json').status_code, 201)

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data['results']
        self.assertEqual([(row['account_name'], row['direction'], row['amount']) for row in rows], [
            (self.bank.name, 'inflow', '20.0000'),
            (self.account.name, 'outflow', '20.0000'),
            (self.account.name, 'inflow', '70.0001'),
            (self.account.name, 'outflow', '70.0001'),
        ])
        self.assertEqual(self.client.get(url, {'account': self.bank.pk}).data['results'], rows[:1])
        self.assertEqual(self.client.get(url, {'account': self.account.pk, 'q': 'Deposit'}).data['results'], rows[1:2])
        self.assertEqual(self.client.get(url, {'account': 'invalid'}).status_code, 400)
        self.assertEqual(self.client.post(url, {}, format='json').status_code, 405)

    def test_cash_flow_page_and_filter_respect_account_permissions_and_warehouse_scope(self):
        api_url = reverse('accounting:cash-flow-list')
        page_url = reverse('accounting_pages:cash-flow-list')
        assignment = assign_warehouse_role(self.staff, warehouse=self.warehouse)
        permissions = Permission.objects.filter(content_type__app_label='accounting', codename__in=[
            'view_financialaccount', 'view_accounttransaction',
        ])
        assignment.role.permissions.set(permissions)
        for destination in (self.bank, self.shared, self.remote):
            response = self.client.post(self.transfers, self.transfer_data('5', destination), format='json')
            self.assertEqual(response.status_code, 201, response.data)
        self.bank.is_active = False
        self.bank.save(update_fields=['is_active'])

        self.client.force_login(self.staff)
        self.assertEqual({row['account'] for row in self.client.get(api_url).data['results']},
                         {self.account.pk, self.bank.pk})
        self.assertEqual(self.client.get(api_url, {'account': self.remote.pk}).data['count'], 0)
        self.assertEqual(self.client.get(api_url, {'account': self.shared.pk}).data['count'], 0)
        page = self.client.get(page_url)
        self.assertContains(page, f'data-api-url="{api_url}"')
        self.assertContains(page, f'href="{page_url}"')
        self.assertContains(page, '<option value="">All accounts</option>', html=True)
        for account in (self.bank, self.account):
            self.assertContains(page, f'<option value="{account.pk}">{account.name}</option>', html=True)
        for account in (self.remote, self.shared):
            self.assertNotContains(page, f'<option value="{account.pk}">{account.name}</option>', html=True)

        assignment.role.permissions.set(permissions.filter(codename='view_financialaccount'))
        self.assertEqual(self.client.get(api_url).status_code, 403)
        self.assertEqual(self.client.get(page_url).status_code, 403)
        self.assertNotContains(self.client.get(reverse('accounting_pages:financial-account-list')), f'href="{page_url}"')

    def test_ledger_uses_effective_dates_and_generation_is_read_only(self):
        for date, amount, kind in [('2026-04-03', '8', 'money_transfer'), ('2026-04-01', '2', 'fund_transfer'),
                                   ('2026-04-02', '3', 'fund_transfer'), ('2026-04-04', '1', 'money_transfer')]:
            posted = self.client.post(self.transfers, {**self.transfer_data(amount), 'date': date,
                                                       'transfer_type': kind}, format='json')
            self.assertEqual(posted.status_code, 201, posted.data)
        url = reverse('accounting:ledger')
        request = {'account': self.account.pk, 'start_date': '2026-04-02', 'end_date': '2026-04-03'}
        result = self.client.post(url, request, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        ledger = result.json()
        self.assertEqual((ledger['opening_balance'], ledger['total_inflow'], ledger['total_outflow'],
                          ledger['closing_balance']), ('98.0001', '0.0000', '11.0000', '87.0001'))
        self.assertEqual([row['running_balance'] for row in ledger['results']], ['95.0001', '87.0001'])
        self.assertTrue(ledger['results'][0]['source'].startswith('Fund transfer TR-'))
        self.assertTrue(ledger['results'][1]['source'].startswith('Money transfer TR-'))
        self.assertEqual(self.client.post(url, request, format='json').json(), ledger)
        self.assertEqual(AccountTransaction.objects.count(), 8)
        bank = self.client.post(url, {**request, 'account': self.bank.pk}, format='json').json()
        self.assertEqual((bank['total_inflow'], bank['closing_balance']), ('11.0000', '13.0000'))
        empty = self.client.post(url, {**request, 'start_date': '2026-05-01', 'end_date': '2026-05-01'},
                                 format='json').json()
        self.assertEqual(empty['results'], [])
        self.assertEqual(empty['opening_balance'], empty['closing_balance'])
        for invalid in ({}, {**request, 'end_date': '2026-04-01'}, {**request, 'start_date': 'invalid'}):
            self.assertEqual(self.client.post(url, invalid, format='json').status_code, 400)

    def test_ledger_page_and_api_require_statement_access_and_scope(self):
        url = reverse('accounting:ledger')
        page_url = reverse('accounting_pages:ledger')
        assignment = assign_warehouse_role(self.staff, warehouse=self.warehouse)
        permissions = Permission.objects.filter(content_type__app_label='accounting', codename__in=[
            'view_financialaccount', 'view_accounttransaction',
        ])
        assignment.role.permissions.set(permissions)
        self.client.force_login(self.staff)
        page = self.client.get(page_url)
        self.assertContains(page, f'action="{url}"')
        self.assertContains(page, f'href="{page_url}"')
        self.assertEqual({a['id'] for a in self.client.get(url).data['accounts']}, {self.account.pk, self.bank.pk})
        request = {'account': self.remote.pk, 'start_date': '2026-01-01', 'end_date': '2026-01-31'}
        self.assertEqual(self.client.post(url, request, format='json').status_code, 404)
        assignment.role.permissions.set(permissions.filter(codename='view_financialaccount'))
        self.assertEqual(self.client.get(page_url).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, request, format='json').status_code, 403)
        self.assertNotContains(self.client.get(reverse('accounting_pages:financial-account-list')), f'href="{page_url}"')

    def test_accounting_pages_link_to_statements_and_expense_completion(self):
        account_list = self.client.get(reverse('accounting_pages:financial-account-list'))
        self.assertContains(account_list, 'accounting-submenu')
        self.assertContains(account_list, reverse('accounting_pages:account-statement', args=[0]))
        statement = self.client.get(reverse('accounting_pages:account-statement', args=[self.account.pk]))
        self.assertContains(statement, reverse('accounting:account-statement', args=[self.account.pk]))
        self.assertContains(statement, 'Main cash statement')
        expense_list_url = reverse('accounting_pages:expense-list')
        expenses = self.client.get(expense_list_url)
        self.assertContains(expenses, reverse('accounting_pages:expense-detail', args=[0]))
        self.assertEqual(self.client.get(f'{expense_list_url}{self.expense.pk}/payments/').status_code, 404)
        self.assertContains(expenses, reverse('accounting:expense-complete'))
        expense_form = self.client.get(reverse('accounting_pages:add-expense'))
        self.assertContains(expense_form, 'name="payment_account"')
        transfer_form = self.client.get(reverse('accounting_pages:add-account-transfer'))
        self.assertContains(transfer_form, f'action="{self.transfers}"')
        self.assertContains(transfer_form, '<input type="hidden" name="transfer_type" value="fund_transfer">', html=True)

    def test_income_category_draft_completion_and_ledger(self):
        category = self.client.post(reverse('accounting:income-category-list'), {'name': 'Room hire'}, format='json')
        self.assertEqual(category.status_code, 201, category.data)
        category_id = category.data['income_category']['id']
        options = self.client.get(reverse('accounting:income-options')).data
        self.assertEqual(options['category'], [{'value': category_id, 'label': 'Room hire'}])
        url = reverse('accounting:income-list')
        created = self.client.post(url, {'category': category_id, 'warehouse': self.warehouse.pk,
                                         'amount': '7', 'payment_account': self.bank.pk}, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        pk = created.data['income']['id']
        detail = reverse('accounting:income-detail', args=[pk])
        self.assertEqual(self.client.patch(detail, {'amount': '8.1234'}, format='json').status_code, 200)
        self.assertFalse(AccountTransaction.objects.exists())
        self.assertEqual(self.client.get(url, {'category': category_id}).data['total_amount'], '8.1234')
        page = self.client.get(reverse('accounting_pages:income-list'))
        self.assertContains(page, reverse('accounting:income-complete'))
        self.assertContains(page, 'Income categories')
        self.assertContains(self.client.get(reverse('accounting_pages:add-income')), 'Receive into account')
        self.assertContains(self.client.get(reverse('accounting_pages:income-detail', args=[pk])), detail)
        self.assertContains(self.client.get(reverse('accounting_pages:edit-income', args=[pk])),
                            reverse('accounting:income-edit-options', args=[pk]))
        self.assertContains(self.client.get(reverse('accounting_pages:add-income-category')), 'name="name"')
        complete = reverse('accounting:income-complete')
        payload = {'income_id': pk, 'is_completed': True}
        response = self.client.post(complete, payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['income']['paid_amount'], '8.1234')
        self.assertFalse(self.client.post(complete, payload, format='json').data['completed_now'])
        self.assertEqual(self.client.patch(detail, {'amount': '10'}, format='json').status_code, 409)
        self.assertEqual(self.client.delete(detail).status_code, 409)
        payments = self.client.get(reverse('accounting:income-payment-list', args=[pk])).data['results']
        self.assertEqual([p['amount'] for p in payments], ['8.1234'])
        self.assertEqual(self.statement(self.bank)['account']['balance'], '8.1234')
        today = timezone.localdate().isoformat()
        ledger = self.client.post(reverse('accounting:ledger'), {'account': self.bank.pk, 'start_date': today,
                                                                 'end_date': today}, format='json').data
        self.assertEqual(ledger['total_inflow'], '8.1234')
        self.assertEqual([r['source'] for r in ledger['results']], [f"Income payment {payments[0]['name']}"])

        assignment = assign_warehouse_role(self.staff, warehouse=self.other_warehouse)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code, 403)
        assignment.role.permissions.set(Permission.objects.filter(content_type__app_label='accounting',
                                                                  codename__in=['view_income', 'change_income']))
        self.assertEqual(self.client.get(url).data['count'], 0)
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertEqual(self.client.post(complete, payload, format='json').status_code, 404)

    def test_daybook_reconciles_with_the_cash_ledger(self):
        daybook = reverse('accounting:daybook')
        for date, amount in [('2025-04-30', '7'), ('2025-05-01', '2.0001')]:
            posted = self.client.post(self.transfers, {**self.transfer_data(amount), 'date': date}, format='json')
            self.assertEqual(posted.status_code, 201)
        filters = {'start_date': '2025-04-30', 'end_date': '2025-04-30'}
        data = self.client.get(daybook, filters).json()
        self.assertEqual((data['count'], data['inflow'], data['outflow']), (1, '7.0000', '7.0000'))
        entry, = data['entries']
        self.assertEqual(entry['reference'], 'Deposit')
        self.assertEqual(entry['source'], 'Fund transfer')
        self.assertEqual(len(entry['lines']), 2)
        data = self.client.get(daybook, {**filters, 'account': self.account.pk, 'q': entry['voucher']}).json()
        self.assertEqual((data['inflow'], data['outflow']), ('0.0000', '7.0000'))
        self.assertEqual(self.client.get(daybook, {**filters, 'status': 'draft'}).status_code, 400)
        self.assertEqual(self.client.get(daybook, {**filters, 'end_date': '2025-04-29'}).status_code, 400)
        self.assertContains(self.client.get(reverse('accounting_pages:daybook'), {**filters, 'source': 'expense'}),
                            'No posted transactions for this period')
        ledger = self.client.post(reverse('accounting:ledger'), {**filters, 'account': self.account.pk}, format='json')
        self.assertEqual(data['outflow'], ledger.json()['total_outflow'])
        self.assertEqual(data['inflow'], ledger.json()['total_inflow'])
        self.assertContains(self.client.get(reverse('accounting_pages:daybook'), filters), entry['voucher'])

        assignment = assign_warehouse_role(self.staff, warehouse=self.warehouse)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(daybook).status_code, 403)
        assignment.role.permissions.set(Permission.objects.filter(content_type__app_label='accounting',
                                       codename__in=['view_financialaccount', 'view_accounttransaction']))
        self.assertEqual(self.client.get(daybook, {'account': self.remote.pk}).status_code, 400)

    def test_reference_display_arithmetic_is_independent_of_cash_flow_convention(self):
        rows = [(Decimal('1000.00'), Decimal('91000.00')), (Decimal('2105.00'), Decimal('30.00'))]
        rows.extend([(Decimal('1105.00'), Decimal('30.00'))] * 19)
        credits = sum(credit for credit, debit in rows)
        signed_debits = -sum(debit for credit, debit in rows)
        self.assertEqual((credits, signed_debits, credits + signed_debits),
                         (Decimal('24100.00'), Decimal('-91600.00'), Decimal('-67500.00')))
        self.assertEqual([credit - debit for credit, debit in rows], [-90000, 2075] + [1075] * 19)
        self.assertContains(self.client.get(reverse('accounting_pages:daybook')),
                            'Both columns show positive amounts. Net movement is inflow minus outflow.')

    def test_daybook_pagination_keeps_vouchers_together_and_totals_cover_all_pages(self):
        for i in range(26):
            posted = self.client.post(self.transfers, {**self.transfer_data('1'), 'date': f'2025-01-{i + 1:02d}'},
                                      format='json')
            self.assertEqual(posted.status_code, 201, posted.data)
        url = reverse('accounting:daybook')
        filters = {'start_date': '2025-01-01', 'end_date': '2025-01-31'}
        first = self.client.get(url, filters).json()
        last = self.client.get(url, {**filters, 'page': 2}).json()
        self.assertEqual([len(first['entries']), len(last['entries'])], [25, 1])
        self.assertEqual(len(last['entries'][0]['lines']), 2)
        self.assertEqual(first['inflow'], last['inflow'])
        self.assertEqual(last['outflow'], '26.0000')
        newest = self.client.get(url, {**filters, 'order': 'newest', 'print': '1'}).json()
        self.assertEqual(len(newest['entries']), 26)
        self.assertEqual(newest['entries'][0]['date'], last['entries'][0]['date'])
