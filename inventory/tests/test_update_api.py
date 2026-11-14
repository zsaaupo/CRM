from common.models import ProductStatus
from io import BytesIO
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from ..models import Brand, Currency, Product, ProductCategory, ProductType, UnitOfMeasure


class InventoryUpdateAPITests(TestCase):
    def setUp(self):
        self.enterContext(override_settings(MEDIA_ROOT=self.enterContext(TemporaryDirectory())))
        self.user = get_user_model().objects.create_user(username='inventory-editor')
        unit = UnitOfMeasure.objects.create(name='Pieces', code='PCS', create_by='original', modified_by='original')
        self.product = Product.objects.create(name='Product', sku='UPD-001', currency_id='BDT', unit=unit,
                                              sale_price='20', minimum_sale_price='10',
                                              create_by='original', modified_by='original')
        self.resources = [
            ('product', self.product, {'sku': 'UPD-001', 'currency': 'BDT', 'unit': unit.pk, 'sale_price': '20'}),
            ('category', ProductCategory.objects.create(name='Category', code='CAT'), {'code': 'CAT'}),
            ('brand', Brand.objects.create(name='Brand'), {}),
            ('unit', unit, {'code': 'PCS'}),
            ('product-type', ProductType.objects.create(name='Rental', code='rental'), {'code': 'rental'}),
            ('status', ProductStatus.objects.create(name='Review', code='review'), {'code': 'review'}),
            ('currency', Currency.objects.create(name='Dollar', code='USD', exchange_rate='120'),
             {'code': 'USD', 'exchange_rate': '120'}),
        ]
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=self.token)

    def permission(self, record, action='change'):
        return Permission.objects.get(content_type__app_label=record._meta.app_label,
                                      codename=f'{action}_{record._meta.model_name}')

    def grant(self, record, action='change'):
        self.user.user_permissions.add(self.permission(record, action))

    def test_each_model_supports_patch_and_put_with_only_its_change_permission(self):
        for route, record, required in self.resources:
            with self.subTest(route=route):
                url = reverse(f'inventory:{route}-update', args=[record.pk])
                self.assertEqual(APIClient().patch(url, {'name': 'Denied'}, format='json').status_code, 403)
                self.assertEqual(self.client.patch(url, {'name': 'Denied'}, format='json').status_code, 403)
                self.grant(record, 'view')
                self.assertEqual(self.client.patch(url, {'name': 'Denied'}, format='json').status_code, 403)
                self.user.user_permissions.remove(self.permission(record, 'view'))
                self.grant(record)
                response = self.client.patch(url, {'name': 'Updated', 'id': 99999, 'create_by': 'spoof',
                                                    'modified_by': 'spoof'}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data['id'], record.pk)
                self.assertEqual(response.data['modified_by'], self.user.username)
                self.assertEqual(response.data['create_by'], record.create_by)
                self.assertIn('no-store', response['Cache-Control'])
                self.assertEqual(response.data['name'], 'Updated')
                response = self.client.put(url, {**required, 'name': 'Replaced'}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                record.refresh_from_db()
                self.assertEqual(record.name, 'Replaced')
                self.assertEqual(self.client.options(url).status_code, 200)

    def test_csrf_and_supported_methods_for_all_models(self):
        for route, record, required in self.resources:
            self.grant(record)
            url = reverse(f'inventory:{route}-update', args=[record.pk])
            self.client.credentials()
            self.assertEqual(self.client.patch(url, {'name': 'Unsafe'}, format='json').status_code, 403)
            self.client.credentials(HTTP_X_CSRFTOKEN=self.token)
            for method in ['get', 'post', 'delete', 'head']:
                with self.subTest(route=route, method=method):
                    self.assertEqual(getattr(self.client, method)(url).status_code, 405)
            self.assertEqual(self.client.patch(reverse(f'inventory:{route}-update', args=[99999]),
                                                {'name': 'Missing'}, format='json').status_code, 404)
            record.refresh_from_db()
            self.assertNotEqual(record.name, 'Unsafe')

    def test_validation_rejects_invalid_merged_product_state_and_unknown_references(self):
        self.grant(self.product)
        url = reverse('inventory:product-update', args=[self.product.pk])
        for invalid in [{'sale_price': '5'}, {'cost_price': '-1'}, {'product_tax': '101'},
                        {'variant_of': self.product.pk}, {'currency': 'ZZZ'}, {'attributes': []}]:
            with self.subTest(payload=invalid):
                response = self.client.patch(url, invalid, format='json')
                self.assertEqual(response.status_code, 400, response.data)
        self.product.refresh_from_db()
        self.assertEqual(str(self.product.sale_price), '20.0000')

    def test_immutable_lookup_codes_and_currency_rate_validation(self):
        for route, record, required in self.resources:
            if route not in ['product-type', 'status', 'currency']:
                continue
            self.grant(record)
            url = reverse(f'inventory:{route}-update', args=[record.pk])
            self.assertEqual(self.client.patch(url, {'code': 'NEW'}, format='json').status_code, 400)
        currency = self.resources[-1][1]
        url = reverse('inventory:currency-update', args=[currency.pk])
        self.assertEqual(self.client.patch(url, {'exchange_rate': '0'}, format='json').status_code, 400)

    def test_product_image_can_be_replaced_and_cleared(self):
        self.grant(self.product)
        url = reverse('inventory:product-update', args=[self.product.pk])
        data = BytesIO()
        Image.new('RGB', (2, 2), 'blue').save(data, 'PNG')
        image = SimpleUploadedFile('updated.png', data.getvalue(), content_type='image/png')
        response = self.client.patch(url, {'image': image, 'attributes': '{"color":"blue"}'}, format='multipart')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['image'].endswith('/products_image/updated.png'))
        self.assertEqual(response.data['attributes'], {'color': 'blue'})
        response = self.client.patch(url, {'image': None}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data['image'])

    def test_group_grants_revocation_and_inactive_account(self):
        brand = self.resources[2][1]
        role = Group.objects.create(name='Brand editors')
        role.permissions.add(self.permission(brand))
        self.user.groups.add(role)
        url = reverse('inventory:brand-update', args=[brand.pk])
        self.assertEqual(self.client.patch(url, {'name': 'Edited'}, format='json').status_code, 200)
        role.permissions.clear()
        self.assertEqual(self.client.patch(url, {'name': 'Denied'}, format='json').status_code, 403)
        self.grant(brand)
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.assertEqual(self.client.patch(url, {'name': 'Denied'}, format='json').status_code, 403)
