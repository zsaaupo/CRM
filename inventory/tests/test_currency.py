from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from ..models import Currency, Product, UnitOfMeasure


class CurrencyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='currency-operator')
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.url = reverse('inventory:currency-list')

    def grant(self, *actions):
        self.user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='inventory', codename__in=[f'{action}_currency' for action in actions]))

    def csrf(self):
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)

    def payload(self, **overrides):
        return {'code': 'USD', 'name': 'US dollar', 'symbol': '$', 'exchange_rate': '120.12345678', **overrides}

    def test_crud_requires_separate_permissions_and_records_audit_fields(self):
        self.csrf()
        self.assertEqual(APIClient().get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.payload(), format='json').status_code, 403)
        self.grant('add')
        response = self.client.post(self.url, self.payload(create_by='spoof'), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        record = response.data['currency']
        self.assertEqual(record['exchange_rate'], '120.12345678')
        self.assertEqual(record['base_currency'], 'BDT')
        self.assertEqual(record['create_by'], self.user.username)
        detail = reverse('inventory:currency-detail', args=[record['id']])
        self.assertEqual(self.client.get(detail).status_code, 403)
        self.assertEqual(self.client.patch(detail, {'name': 'Dollar'}, format='json').status_code, 403)
        self.grant('view', 'change')
        self.assertEqual(self.client.get(detail).status_code, 200)
        changed = self.client.patch(detail, {'exchange_rate': '121.50000001', 'is_active': False}, format='json')
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertEqual(changed.data['modified_by'], self.user.username)
        self.assertEqual(changed.data['exchange_rate'], '121.50000001')
        self.assertFalse(changed.data['is_active'])
        self.assertEqual(self.client.put(detail, self.payload(name='Dollar'), format='json').status_code, 200)
        self.assertEqual(self.client.delete(detail).status_code, 403)
        self.grant('delete')
        self.assertEqual(self.client.delete(detail).status_code, 204)

    def test_validation_and_csrf(self):
        self.grant('add')
        self.assertEqual(self.client.post(self.url, self.payload(), format='json').status_code, 403)
        self.csrf()
        cases = [{'exchange_rate': '0'}, {'exchange_rate': '-1'}, {'exchange_rate': None},
                 {'exchange_rate': 'NaN'}, {'exchange_rate': '1.123456789'}, {'code': 'usd'},
                 {'code': 'US'}, {'code': 'TOOLONG'}]
        for invalid in cases:
            with self.subTest(invalid=invalid):
                self.assertEqual(self.client.post(self.url, self.payload(**invalid), format='json').status_code, 400)
        missing = self.payload()
        del missing['exchange_rate']
        self.assertEqual(self.client.post(self.url, missing, format='json').status_code, 400)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Currency.objects.create(code='EUR', name='Euro', exchange_rate=0)

    def test_codes_are_immutable_base_rate_is_one_and_used_currency_cannot_be_deleted(self):
        self.grant('view', 'change', 'delete')
        self.csrf()
        base = Currency.objects.get(code='BDT')
        detail = reverse('inventory:currency-detail', args=[base.pk])
        self.assertEqual(self.client.patch(detail, {'code': 'USD'}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(detail, {'exchange_rate': '2'}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(detail, {'exchange_rate': '1'}, format='json').status_code, 200)
        unit = UnitOfMeasure.objects.create(name='Pieces', code='PCS')
        product = Product.objects.create(name='Sample', sku='CURRENCY-001', currency=base, unit=unit)
        self.assertEqual(self.client.delete(detail).status_code, 409)
        product.refresh_from_db()
        self.assertEqual(product.currency_id, 'BDT')

    def test_list_search_pagination_and_read_only_endpoint(self):
        self.grant('view')
        Currency.objects.create(**self.payload())
        response = self.client.get(reverse('inventory:currency-list-view'), {'q': 'usd'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['code'], 'USD')
        self.csrf()
        self.assertEqual(self.client.post(reverse('inventory:currency-list-view'), self.payload(), format='json').status_code, 405)

    def test_pages_edit_permissions_initial_values_and_redirect_destination(self):
        currency = Currency.objects.create(**self.payload())
        listing = reverse('inventory_pages:currency-list')
        edit = reverse('inventory_pages:edit-currency', args=[currency.pk])
        self.assertEqual(self.client.get(edit).status_code, 403)
        self.grant('view')
        response = self.client.get(listing)
        self.assertContains(response, 'Currency list')
        self.assertNotContains(response, '"type": "edit"')
        self.assertEqual(self.client.get(edit).status_code, 403)
        self.grant('change')
        self.assertContains(self.client.get(listing), '"type": "edit"')
        response = self.client.get(edit)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Edit currency')
        self.assertNotContains(response, 'value="120.12345678"')
        self.assertContains(response, f'data-detail-url="{reverse("inventory:currency-detail", args=[currency.pk])}"')
        self.assertContains(response, 'data-method="PATCH"')
        self.assertContains(response, f'data-success-url="{listing}"')
        self.assertContains(response, f'action="{reverse("inventory:currency-update", args=[currency.pk])}"')
        self.assertNotIn('form', response.context)
        self.assertEqual(self.client.get(reverse('inventory_pages:edit-currency', args=[9999])).status_code, 200)
        self.assertEqual(self.client.get(reverse('inventory:currency-detail', args=[9999])).status_code, 404)


class CurrencyMigrationTests(TransactionTestCase):
    before = [('inventory', '0003_producttype_status_and_more')]
    after = [('inventory', '0004_currency_product_currency')]

    def restore(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_existing_currency_codes_and_prices_survive_migration(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        self.addCleanup(self.restore)
        apps = executor.loader.project_state(self.before).apps
        Unit = apps.get_model('inventory', 'UnitOfMeasure')
        OldProduct = apps.get_model('inventory', 'Product')
        apps.get_model('inventory', 'ProductType').objects.get_or_create(code='goods', defaults={'name': 'Physical goods'})
        apps.get_model('inventory', 'Status').objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        unit = Unit.objects.create(name='Migration unit', code='MIG')
        ids = [OldProduct.objects.create(name=f'Legacy {index}', sku=f'CUR-{index}', unit=unit,
                                        currency=code, sale_price='123.4567').pk
               for index, code in enumerate(['BDT', 'USD', 'USD', 'EUR'])]
        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        apps = executor.loader.project_state(self.after).apps
        NewProduct = apps.get_model('inventory', 'Product')
        NewCurrency = apps.get_model('inventory', 'Currency')
        self.assertEqual(NewCurrency.objects.count(), 3)
        self.assertEqual(NewCurrency.objects.get(code='BDT').exchange_rate, Decimal('1'))
        self.assertIsNone(NewCurrency.objects.get(code='USD').exchange_rate)
        for pk, code in zip(ids, ['BDT', 'USD', 'USD', 'EUR']):
            product = NewProduct.objects.select_related('currency').get(pk=pk)
            self.assertEqual(product.currency.code, code)
            self.assertEqual(product.sale_price, Decimal('123.4567'))
