from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
from rest_framework.test import APIClient

from ..models import Brand, Product, ProductCategory, ProductType, Status, UnitOfMeasure
from ..serializers import ProductSerializer


User = get_user_model()


class InventoryAPITests(TestCase):
    def setUp(self):
        media_root = self.enterContext(TemporaryDirectory())
        self.enterContext(override_settings(MEDIA_ROOT=media_root))
        self.user = User.objects.create_user(username='catalog@example.com')
        self.unit = UnitOfMeasure.objects.create(name='Pieces', code='PCS')
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=self.token)

    def permission(self, model, action):
        return Permission.objects.get(content_type__app_label='inventory',
                                      codename=f'{action}_{model._meta.model_name}')

    def grant(self, model, *actions):
        self.user.user_permissions.add(*(self.permission(model, action) for action in actions))

    def resources(self):
        return [
            (ProductCategory, 'category', 'category', {'code': 'API-CAT', 'name': 'API category'}),
            (Brand, 'brand', 'brand', {'name': 'API brand', 'website': 'https://example.com'}),
            (UnitOfMeasure, 'unit', 'unit', {'code': 'API-UNIT', 'name': 'API unit'}),
            (ProductType, 'product-type', 'product_type', {'code': 'rental', 'name': 'API type'}),
            (Status, 'status', 'status', {'code': 'review', 'name': 'API status'}),
            (Product, 'product', 'product', {'sku': 'API-001', 'name': 'API product', 'currency': 'BDT',
                                          'unit': self.unit.pk, 'product_type': 'goods', 'status': 'draft',
                                          'sale_price': '20.0000', 'attributes': {'color': 'blue'}}),
        ]

    def test_each_model_supports_crud_with_separate_action_permissions(self):
        for model, route, key, payload in self.resources():
            with self.subTest(model=model.__name__):
                url = reverse(f'inventory:{route}-list')
                self.assertEqual(self.client.post(url, payload, format='json').status_code, 403)
                self.grant(model, 'add')
                created = self.client.post(url, {**payload, 'id': 99999, 'create_by': 'spoof',
                                                 'modified_by': 'spoof'}, format='json')
                self.assertEqual(created.status_code, 201, created.data)
                record = created.data[key]
                self.assertNotEqual(record['id'], 99999)
                self.assertEqual(record['create_by'], self.user.username)
                self.assertEqual(record['modified_by'], self.user.username)
                self.assertIn('no-store', created['Cache-Control'])
                detail = reverse(f'inventory:{route}-detail', args=[record['id']])
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.get(detail).status_code, 403)
                self.assertEqual(self.client.patch(detail, {'name': 'Updated'}, format='json').status_code, 403)
                self.grant(model, 'view', 'change')
                listing = self.client.get(url, {'q': payload['name']})
                self.assertEqual(listing.status_code, 200, listing.data)
                self.assertEqual(listing.data['count'], 1)
                self.assertEqual(self.client.get(detail).status_code, 200)
                self.assertEqual(self.client.head(detail).status_code, 200)
                updated = self.client.patch(detail, {'name': 'Updated'}, format='json')
                self.assertEqual(updated.status_code, 200, updated.data)
                replaced = self.client.put(detail, {**payload, 'name': 'Replaced'}, format='json')
                self.assertEqual(replaced.status_code, 200, replaced.data)
                self.assertEqual(replaced.data['name'], 'Replaced')
                self.assertEqual(self.client.delete(detail).status_code, 403)
                self.grant(model, 'delete')
                self.assertEqual(self.client.delete(detail).status_code, 204)
                self.assertFalse(model.objects.filter(pk=record['id']).exists())
                self.assertEqual(self.client.get(detail).status_code, 404)

    def test_anonymous_and_inactive_users_are_denied(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        for client in [APIClient(), self.client]:
            for model, route, key, payload in self.resources():
                with self.subTest(route=route):
                    self.assertEqual(client.get(reverse(f'inventory:{route}-list')).status_code, 403)
                    self.assertEqual(client.post(reverse(f'inventory:{route}-list'), payload, format='json').status_code, 403)
                    self.assertEqual(client.get(reverse(f'inventory:{route}-detail', args=[999])).status_code, 403)

    def test_csrf_is_required_for_creation(self):
        self.client.credentials()
        for model, route, key, payload in self.resources():
            self.grant(model, 'add')
            url = reverse(f'inventory:{route}-list')
            self.assertEqual(self.client.post(url, payload, format='json').status_code, 403)
            self.assertEqual(self.client.post(url, payload, format='json', HTTP_X_CSRFTOKEN='invalid').status_code, 403)

    def test_group_permissions_grant_access_but_account_add_user_does_not(self):
        self.user.user_permissions.add(Permission.objects.get(content_type__app_label='auth', codename='add_user'))
        for model, route, key, payload in self.resources():
            self.assertEqual(self.client.post(reverse(f'inventory:{route}-list'), payload, format='json').status_code, 403)
        role = Group.objects.create(name='Catalog creators')
        role.permissions.add(self.permission(Brand, 'add'), self.permission(Brand, 'view'))
        self.user.groups.add(role)
        self.assertEqual(self.client.post(reverse('inventory:brand-list'), {'name': 'Permitted brand'}, format='json').status_code, 201)
        self.assertEqual(self.client.get(reverse('inventory:brand-list')).status_code, 200)
        role.permissions.clear()
        self.assertEqual(self.client.get(reverse('inventory:brand-list')).status_code, 403)

    def test_partial_product_validation_does_not_persist_invalid_changes(self):
        product = Product.objects.create(sku='P-001', name='Product', currency_id='BDT', unit=self.unit,
                                         sale_price=20, minimum_sale_price=15)
        self.grant(Product, 'change')
        detail = reverse('inventory:product-detail', args=[product.pk])
        for payload in [{'sale_price': 10}, {'attributes': []}, {'variant_of': product.pk},
                        {'product_type': 'unknown'}, {'status': 'unknown'},
                        {'unit': self.unit.pk + 999}, {'product_tax': 101}]:
            with self.subTest(payload=payload):
                response = self.client.patch(detail, payload, format='json')
                self.assertEqual(response.status_code, 400, response.data)
        product.refresh_from_db()
        self.assertEqual(product.sale_price, 20)
        self.assertIsNone(product.variant_of_id)

    def test_protected_relations_and_default_lookups_return_conflict(self):
        brand = Brand.objects.create(name='Referenced brand')
        category = ProductCategory.objects.create(code='REF', name='Referenced category')
        product_type = ProductType.objects.create(code='physical', name='Physical')
        product_status = Status.objects.create(code='review', name='Review')
        parent = Product.objects.create(sku='P-001', name='Parent', currency_id='BDT', unit=self.unit,
                                        brand=brand, category=category, product_type=product_type, status=product_status)
        Product.objects.create(sku='P-002', name='Variant', currency_id='BDT', unit=self.unit,
                               product_type=product_type, variant_of=parent)
        records = [(Brand, 'brand', brand), (ProductCategory, 'category', category),
                   (UnitOfMeasure, 'unit', self.unit), (ProductType, 'product-type', product_type),
                   (Status, 'status', product_status), (Product, 'product', parent),
                   (ProductType, 'product-type', ProductType.objects.get(code='goods')),
                   (Status, 'status', Status.objects.get(code='draft'))]
        for model, route, record in records:
            self.grant(model, 'delete')
            response = self.client.delete(reverse(f'inventory:{route}-detail', args=[record.pk]))
            self.assertEqual(response.status_code, 409, response.data)
            self.assertTrue(model.objects.filter(pk=record.pk).exists())

    def test_product_type_and_status_codes_cannot_be_changed(self):
        for model, route, code in [(ProductType, 'product-type', 'goods'), (Status, 'status', 'draft')]:
            self.grant(model, 'change')
            record = model.objects.get(code=code)
            response = self.client.patch(reverse(f'inventory:{route}-detail', args=[record.pk]),
                                         {'code': 'changed'}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            record.refresh_from_db()
            self.assertEqual(record.code, code)

    def test_product_image_upload_and_invalid_image_validation(self):
        self.grant(Product, 'add')
        content = BytesIO()
        Image.new('RGB', (2, 2)).save(content, format='PNG')
        data = {'sku': 'IMAGE-001', 'name': 'Image product', 'unit': self.unit.pk, 'currency': 'BDT',
                'attributes': '{"color":"blue"}',
                'image': SimpleUploadedFile('product.png', content.getvalue(), content_type='image/png')}
        response = self.client.post(reverse('inventory:product-list'), data, format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        product = Product.objects.get(pk=response.data['product']['id'])
        self.assertTrue(product.image.name.startswith('products_image/'))
        self.assertTrue(response.data['product']['image'].endswith(product.image.url))
        self.assertEqual(product.attributes, {'color': 'blue'})
        data.update(sku='IMAGE-002', image=SimpleUploadedFile('bad.png', b'invalid', content_type='image/png'))
        response = self.client.post(reverse('inventory:product-list'), data, format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertIn('image', response.data)
        self.assertFalse(Product.objects.filter(sku='IMAGE-002').exists())

    def test_case_insensitive_duplicates_are_validation_errors(self):
        self.grant(Product, 'add')
        Product.objects.create(sku='SKU-001', name='Existing', currency_id='BDT', unit=self.unit)
        response = self.client.post(reverse('inventory:product-list'), {
            'sku': ' sku-001 ', 'name': 'Duplicate', 'currency': 'BDT', 'unit': self.unit.pk,
        }, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Product.objects.count(), 1)

    def test_database_conflicts_are_reported_without_partial_records(self):
        self.grant(Product, 'add')
        with patch('inventory.models.Product.save', side_effect=IntegrityError('simulated conflict')):
            response = self.client.post(reverse('inventory:product-list'), {
                'sku': 'CONFLICT', 'name': 'Conflict', 'currency': 'BDT', 'unit': self.unit.pk,
            }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Product.objects.exists())

    def test_list_pagination_and_search(self):
        UnitOfMeasure.objects.bulk_create([UnitOfMeasure(name=f'Unit {i:02}', code=f'U{i}') for i in range(21)])
        self.grant(UnitOfMeasure, 'view')
        page = self.client.get(reverse('inventory:unit-list'))
        self.assertEqual(page.data['count'], 22)
        self.assertEqual(len(page.data['results']), 20)
        self.assertEqual(len(self.client.get(page.data['next']).data['results']), 2)
        search = self.client.get(reverse('inventory:unit-list'), {'q': 'PCS'})
        self.assertEqual(search.data['count'], 1)
        self.assertEqual(search.data['results'][0]['id'], self.unit.pk)

    def test_product_serialization_fetches_related_models_in_one_query(self):
        Product.objects.create(sku='P-001', name='Product', currency_id='BDT', unit=self.unit)
        with self.assertNumQueries(1):
            rows = ProductSerializer(Product.objects.select_related(
                'product_type', 'status', 'category', 'brand', 'unit', 'variant_of', 'currency'), many=True).data
        self.assertEqual(rows[0]['product_type'], 'goods')
        self.assertEqual(rows[0]['status'], 'draft')
