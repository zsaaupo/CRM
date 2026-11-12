from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from ..models import Brand, Currency, Product, ProductCategory, UnitOfMeasure


class ProductOptionsAPITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='product-creator')
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.url = reverse('inventory:product-options')
        self.unit = UnitOfMeasure.objects.create(name='Pieces', code='PCS')
        ProductCategory.objects.create(name='Category <test>', code='CAT', description='Private description')
        Brand.objects.create(name='Example brand')
        self.base = Product.objects.create(name='Base product', sku='BASE', currency_id='BDT', unit=self.unit,
                                            cost_price='99', attributes={'private': 'specifications'})
        Product.objects.create(name='Variant', sku='CHILD', currency_id='BDT', unit=self.unit, variant_of=self.base)

    def grant(self, codename):
        self.user.user_permissions.add(Permission.objects.get(content_type__app_label='inventory', codename=codename))

    def test_authentication_and_product_add_permission_are_required(self):
        self.assertEqual(APIClient().get(self.url).status_code, 403)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.grant('view_product')
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.grant('add_product')
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_creators_get_only_reference_labels_and_identifiers_without_management_grants(self):
        self.grant('add_product')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data), {'product_type', 'status', 'category', 'brand', 'unit', 'currency', 'variant_of'})
        for choices in data.values():
            for item in choices:
                self.assertEqual(set(item), {'value', 'label'})
        self.assertEqual(data['variant_of'], [{'value': self.base.pk, 'label': 'BASE — Base product'}])
        self.assertIn({'value': 'BDT', 'label': 'BDT (BDT)'}, data['currency'])
        self.assertIn({'value': 'goods', 'label': 'Physical goods'}, data['product_type'])
        self.assertEqual(data['unit'][0]['value'], self.unit.pk)
        self.assertIn('no-store', response['Cache-Control'])
        for route in ['currency', 'category', 'brand', 'unit']:
            self.assertEqual(self.client.get(reverse(f'inventory:{route}-list')).status_code, 403)
        self.assertEqual(self.client.head(self.url).status_code, 200)

    def test_group_grants_revocation_and_inactive_users(self):
        role = Group.objects.create(name='Product creators')
        role.permissions.add(Permission.objects.get(content_type__app_label='inventory', codename='add_product'))
        self.user.groups.add(role)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        role.permissions.clear()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.grant('add_product')
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_endpoint_is_read_only_and_supports_empty_reference_tables(self):
        self.grant('add_product')
        Brand.objects.all().delete()
        self.assertEqual(self.client.get(self.url).json()['brand'], [])
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)
        count = Currency.objects.count()
        for method in ['post', 'put', 'patch', 'delete']:
            response = getattr(self.client, method)(self.url, {}, format='json')
            self.assertEqual(response.status_code, 405)
        self.assertEqual(Currency.objects.count(), count)
