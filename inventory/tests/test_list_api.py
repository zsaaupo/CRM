from common.models import Status
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from ..models import Brand, Product, ProductCategory, ProductType, UnitOfMeasure


class InventoryListAPITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='reader@example.com')
        unit = UnitOfMeasure.objects.create(name='List unit', code='LIST-UNIT')
        category = ProductCategory.objects.create(name='List category', code='LIST-CATEGORY')
        brand = Brand.objects.create(name='List brand')
        product_type = ProductType.objects.create(name='List type', code='list-type')
        status = Status.objects.create(name='List status', code='list-status')
        product = Product.objects.create(sku='LIST-PRODUCT', name='List product', currency_id='BDT',
                                         unit=unit, category=category, brand=brand,
                                         product_type=product_type, status=status,
                                         image='products_image/example.png')
        self.resources = [('product', Product, product), ('category', ProductCategory, category),
                          ('brand', Brand, brand), ('unit', UnitOfMeasure, unit),
                          ('product-type', ProductType, product_type), ('status', Status, status)]
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)

    def grant(self, model, *actions):
        self.user.user_permissions.add(*[
            Permission.objects.get(content_type__app_label=model._meta.app_label,
                                   codename=f'{action}_{model._meta.model_name}')
            for action in actions
        ])

    def test_all_lists_require_authentication_and_model_view_permission(self):
        for route, model, record in self.resources:
            with self.subTest(route=route):
                url = reverse(f'inventory:{route}-list-view')
                self.assertEqual(APIClient().get(url).status_code, 403)
                self.assertEqual(self.client.get(url).status_code, 403)
                self.grant(model, 'view')
                response = self.client.get(url, {'q': record.name})
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data['count'], 1)
                self.assertEqual(response.data['results'][0]['id'], record.pk)
                self.assertEqual(self.client.head(url).status_code, 200)
                self.assertEqual(self.client.options(url).status_code, 200)
                self.assertIn('no-store', response['Cache-Control'])

    def test_all_lists_reject_writes_even_with_write_permissions_and_csrf(self):
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)
        for route, model, record in self.resources:
            self.grant(model, 'view', 'add', 'change', 'delete')
            count = model.objects.count()
            for method in ['post', 'put', 'patch', 'delete']:
                with self.subTest(route=route, method=method):
                    response = getattr(self.client, method)(reverse(f'inventory:{route}-list-view'),
                                                            {'name': 'Changed'}, format='json')
                    self.assertEqual(response.status_code, 405, response.data)
            self.assertEqual(model.objects.count(), count)
            record.refresh_from_db()
            self.assertNotEqual(record.name, 'Changed')

    def test_product_search_and_image_match_existing_list(self):
        self.grant(Product, 'view')
        response = self.client.get(reverse('inventory:product-list-view'), {'q': 'list-product'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertTrue(response.data['results'][0]['image'].endswith('/media/products_image/example.png'))
        existing = self.client.get(reverse('inventory:product-list'), {'q': 'list-product'})
        self.assertEqual(response.data, existing.data)

    def test_pagination_and_empty_results(self):
        self.grant(Brand, 'view')
        Brand.objects.bulk_create([Brand(name=f'Extra brand {index:02}') for index in range(22)])
        url = reverse('inventory:brand-list-view')
        page = self.client.get(url)
        self.assertEqual(page.data['count'], 23)
        self.assertEqual(len(page.data['results']), 20)
        self.assertEqual(len(self.client.get(page.data['next']).data['results']), 3)
        self.assertEqual(self.client.get(url, {'page': 99}).status_code, 404)
        self.assertEqual(self.client.get(url, {'q': 'no matching brand'}).data['results'], [])

    def test_group_grants_and_revocation_apply_to_read_only_list(self):
        role = Group.objects.create(name='Brand readers')
        role.permissions.add(Permission.objects.get(content_type__app_label='inventory', codename='view_brand'))
        self.user.groups.add(role)
        url = reverse('inventory:brand-list-view')
        self.assertEqual(self.client.get(url).status_code, 200)
        role.permissions.clear()
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_inactive_user_cannot_list_records(self):
        self.grant(Brand, 'view')
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.assertEqual(self.client.get(reverse('inventory:brand-list-view')).status_code, 403)
