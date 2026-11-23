from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from warehouse.models import WareHouse
from warehouse.tests.helpers import assign_warehouse_role

from .models import Expense, ExpenseCategory, FinancialAccount


class ExpenseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_user(username='expense-staff', is_staff=True)
        cls.root = get_user_model().objects.create_superuser(username='expense-admin')
        cls.warehouse = WareHouse.objects.create(name='Main warehouse', address='Main', phone='1')
        cls.other_warehouse = WareHouse.objects.create(name='Separate warehouse', address='Elsewhere', phone='2')
        cls.payment_account = FinancialAccount.objects.create(name='Expense bank', type='bank', warehouse=cls.warehouse)
        cls.role = assign_warehouse_role(cls.staff, warehouse=cls.warehouse).role
        cls.category = ExpenseCategory.objects.create(name='Transport')
        cls.other_category = ExpenseCategory.objects.create(name='Utilities')

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.staff)
        self.client.credentials(HTTP_X_CSRFTOKEN=self.client.get(reverse('account:login')).data['csrfToken'])
        self.list_url = reverse('accounting:expense-list')
        self.categories_url = reverse('accounting:expense-category-list')
        self.today = timezone.localdate()
        self.grant(self.role.permissions, 'view_expense', 'add_expense', 'change_expense', 'delete_expense')

    def grant(self, manager, *codenames):
        manager.add(*Permission.objects.filter(content_type__app_label='accounting', codename__in=codenames))

    def payload(self, **changes):
        return {'category': self.category.pk, 'warehouse': self.warehouse.pk, 'amount': '127.3456',
                'payment_account': self.payment_account.pk, **changes}

    def create(self, **changes):
        response = self.client.post(self.list_url, self.payload(**changes), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return Expense.objects.get(pk=response.data['expense']['id'])

    def test_expense_crud_requires_positive_amount_and_keeps_audit_fields(self):
        expense = self.create(create_by='forged')
        self.assertEqual(expense.date, self.today)
        self.assertEqual(expense.amount, Decimal('127.3456'))
        self.assertEqual(expense.create_by, self.staff.username)
        detail = reverse('accounting:expense-detail', args=[expense.pk])
        response = self.client.get(detail)
        self.assertEqual(response.data['currency'], settings.INVENTORY_BASE_CURRENCY)
        self.assertEqual(response.data['category_name'], 'Transport')
        self.assertEqual(response.data['warehouse_name'], self.warehouse.name)
        for amount in ('0', '-2', '0.00001'):
            with self.subTest(amount=amount):
                response = self.client.post(self.list_url, self.payload(amount=amount), format='json')
                self.assertEqual(response.status_code, 400)
                self.assertIn('amount', response.data)
        response = self.client.patch(detail, {'amount': '18.0001'}, format='json')
        self.assertEqual(response.status_code, 200)
        expense.refresh_from_db()
        self.assertEqual(expense.amount, Decimal('18.0001'))
        self.assertEqual(expense.modified_by, self.staff.username)
        self.assertEqual(self.client.delete(detail).status_code, 204)
        self.assertFalse(Expense.objects.filter(pk=expense.pk).exists())

    def test_totals_include_all_matching_pages_and_warehouse_scope(self):
        yesterday = self.today - timedelta(days=1)
        Expense.objects.bulk_create([
            Expense(category=self.category, warehouse=self.warehouse, date=yesterday, amount=Decimal('0.0001'))
            for _ in range(21)
        ])
        newest = self.create(date=self.today.isoformat(), amount='99999999999.9999')
        Expense.objects.create(category=self.other_category, warehouse=self.warehouse, date=self.today, amount=100)
        Expense.objects.create(category=self.category, warehouse=self.other_warehouse, date=self.today, amount=1000)
        Expense.objects.create(category=self.category, warehouse=self.warehouse, date=yesterday - timedelta(days=1), amount=10)
        filters = {'from_date': yesterday.isoformat(), 'to_date': self.today.isoformat(),
                   'category': self.category.pk, 'warehouse': self.warehouse.pk}
        result = self.client.get(self.list_url, filters).data
        self.assertEqual(result['count'], 22)
        self.assertEqual(len(result['results']), 20)
        self.assertEqual(result['results'][0]['id'], newest.pk)
        self.assertEqual(result['total_amount'], '100000000000.0020')
        page_two = self.client.get(self.list_url, {**filters, 'page': 2}).data
        self.assertEqual(len(page_two['results']), 2)
        self.assertEqual(page_two['total_amount'], result['total_amount'])
        result = self.client.get(self.list_url, {**filters, 'warehouse': self.other_warehouse.pk}).data
        self.assertEqual((result['count'], result['total_amount']), (0, '0.0000'))
        response = self.client.get(self.list_url, {'from_date': self.today.isoformat(), 'to_date': yesterday.isoformat()})
        self.assertEqual(response.status_code, 400)
        self.assertIn('to_date', response.data)

    def test_category_crud_blocks_duplicates_and_preserves_expense_history(self):
        self.assertEqual(self.client.get(self.categories_url).status_code, 403)
        self.grant(self.staff.user_permissions, 'view_expensecategory', 'add_expensecategory',
                   'change_expensecategory', 'delete_expensecategory')
        response = self.client.post(self.categories_url, {'name': '  Office supplies  '}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['expense_category']['name'], 'Office supplies')
        self.assertEqual(self.client.post(self.categories_url, {'name': 'transport'}, format='json').status_code, 400)
        used_expense = self.create()
        category = reverse('accounting:expense-category-detail', args=[self.category.pk])
        self.assertEqual(self.client.delete(category).status_code, 409)
        self.assertEqual(self.client.patch(category, {'is_active': False}, format='json').status_code, 200)
        self.assertEqual(self.client.post(self.list_url, self.payload(), format='json').status_code, 400)
        options = self.client.get(reverse('accounting:expense-options')).data
        self.assertEqual([item['value'] for item in options['category']],
                         [response.data['expense_category']['id'], self.other_category.pk])
        options = self.client.get(reverse('accounting:expense-edit-options', args=[used_expense.pk])).data
        self.assertIn(self.category.pk, [item['value'] for item in options['category']])
        detail = reverse('accounting:expense-detail', args=[used_expense.pk])
        self.assertEqual(self.client.patch(detail, self.payload(amount='3'), format='json').status_code, 200)
        self.assertEqual(self.client.get(detail).data['category_name'], 'Transport')

    def test_pages_and_api_enforce_warehouse_scope_and_role_permissions(self):
        expense = self.create()
        other = Expense.objects.create(category=self.category, warehouse=self.other_warehouse, amount=9)
        options = self.client.get(reverse('accounting:expense-options')).data
        self.assertEqual([item['value'] for item in options['warehouse']], [self.warehouse.pk])
        self.assertEqual(self.client.post(self.list_url, self.payload(warehouse=self.other_warehouse.pk),
                                          format='json').status_code, 403)
        detail = reverse('accounting:expense-detail', args=[other.pk])
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertEqual(self.client.patch(detail, {'amount': '1'}, format='json').status_code, 404)
        self.assertEqual(self.client.delete(detail).status_code, 404)
        self.assertEqual(self.client.get(reverse('accounting_pages:edit-expense', args=[other.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('accounting_pages:expense-detail', args=[other.pk])).status_code, 404)
        listing = self.client.get(reverse('accounting_pages:expense-list'))
        self.assertContains(listing, 'Expense list')
        self.assertContains(listing, '<span>Expenses</span>')
        self.assertContains(listing, 'aria-controls="expense-submenu" aria-expanded="true"')
        self.assertNotContains(listing, 'accounting-submenu')
        self.assertNotContains(listing, reverse('accounting_pages:expense-category-list'))
        self.grant(self.staff.user_permissions, 'view_expensecategory')
        categories = self.client.get(reverse('accounting_pages:expense-category-list'))
        self.assertContains(categories, 'aria-controls="expense-submenu" aria-expanded="true"')
        self.assertContains(categories, 'aria-current="page">Expense categories')
        self.assertContains(listing, 'data-catalog-filters')
        self.assertContains(listing, 'Total for matching expenses')
        self.assertNotContains(listing, self.other_warehouse.name)
        form = self.client.get(reverse('accounting_pages:add-expense'))
        self.assertContains(form, 'data-locked="true"')
        self.assertContains(form, f'data-options-url="{reverse("accounting:expense-options")}"')
        edit = self.client.get(reverse('accounting_pages:edit-expense', args=[expense.pk]))
        self.assertContains(edit, f'data-options-url="{reverse("accounting:expense-edit-options", args=[expense.pk])}"')
        page_url = reverse('accounting_pages:expense-detail', args=[expense.pk])
        page = self.client.get(page_url)
        self.assertContains(page, reverse('accounting:expense-detail', args=[expense.pk]))
        self.assertContains(page, reverse('accounting_pages:edit-expense', args=[expense.pk]))
        self.assertContains(page, 'data-detail-field="outstanding_amount"')

        self.role.permissions.remove(Permission.objects.get(codename='change_expense', content_type__app_label='accounting'))
        self.assertNotContains(self.client.get(page_url), 'Edit expense')

        self.grant(self.staff.user_permissions, 'view_expense')
        self.role.permissions.clear()
        self.assertEqual(self.client.get(self.list_url).status_code, 403)
        self.assertEqual(self.client.get(reverse('accounting_pages:expense-list')).status_code, 403)
        self.assertEqual(self.client.get(page_url).status_code, 403)

