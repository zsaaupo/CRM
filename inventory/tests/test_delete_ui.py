from common.models import ProductStatus
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from ..models import Brand, Currency, Product, ProductCategory, ProductType, UnitOfMeasure


class InventoryDeleteUITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='catalog-deleter')
        unit = UnitOfMeasure.objects.create(name='Pieces', code='PCS')
        self.product = Product.objects.create(name='Delete product', sku='DEL-001', unit=unit, currency_id='BDT')
        self.resources = [
            ('product', self.product),
            ('category', ProductCategory.objects.create(name='Delete category', code='CAT')),
            ('brand', Brand.objects.create(name='Delete brand')),
            ('unit', unit),
            ('product-type', ProductType.objects.create(name='Rental', code='rental')),
            ('status', ProductStatus.objects.create(name='Review', code='review')),
            ('currency', Currency.objects.create(name='Euro', code='EUR', exchange_rate='130')),
        ]
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)

    def grant(self, record, action):
        permission = Permission.objects.get(content_type__app_label=record._meta.app_label,
                                            codename=f'{action}_{record._meta.model_name}')
        self.user.user_permissions.add(permission)

    def test_all_lists_expose_delete_confirmation_and_existing_api_only_with_delete_grant(self):
        for route, record in self.resources:
            with self.subTest(route=route):
                self.grant(record, 'view')
                listing = reverse(f'inventory_pages:{route}-list')
                response = self.client.get(listing)
                self.assertNotContains(response, 'data-catalog-delete-dialog')
                self.assertNotIn('delete_url_template', response.context)
                detail = reverse(f'inventory:{route}-detail', args=[record.pk])
                self.client.credentials(HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)
                self.assertEqual(self.client.delete(detail).status_code, 403)
                self.grant(record, 'delete')
                response = self.client.get(listing)
                self.assertContains(response, 'data-catalog-delete-dialog')
                self.assertContains(response, 'This cannot be undone.')
                self.assertContains(response, 'name="csrfmiddlewaretoken"')
                self.assertContains(response, f'data-delete-url-template="{reverse(f"inventory:{route}-detail", args=[0])}"')
                self.assertNotIn('edit_url_template', response.context)
                self.assertEqual(response.context['columns'][-1]['key'], 'actions')
                self.client.credentials()
                self.assertEqual(self.client.delete(detail).status_code, 403)
                self.client.credentials(HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)
                self.assertEqual(self.client.delete(detail).status_code, 204)
                self.assertFalse(type(record).objects.filter(pk=record.pk).exists())
                self.assertEqual(self.client.delete(detail).status_code, 404)

    def test_used_records_default_lookups_and_products_with_variants_return_conflict(self):
        category = self.resources[1][1]
        brand = self.resources[2][1]
        self.product.category = category
        self.product.brand = brand
        self.product.save()
        variant = Product.objects.create(name='Child', sku='CHILD', unit=self.product.unit,
                                         currency_id='BDT', variant_of=self.product)
        protected = [
            ('category', category), ('brand', brand), ('unit', self.product.unit),
            ('currency', Currency.objects.get(code='BDT')),
            ('product-type', ProductType.objects.get(code='goods')),
            ('status', ProductStatus.objects.get(code='draft')), ('product', self.product),
        ]
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)
        for route, record in protected:
            self.grant(record, 'delete')
            response = self.client.delete(reverse(f'inventory:{route}-detail', args=[record.pk]))
            self.assertEqual(response.status_code, 409, response.data)
            self.assertTrue(type(record).objects.filter(pk=record.pk).exists())
        variant.delete()
        self.product.delete()
        for route, model, code in [('product-type', ProductType, 'goods'), ('status', ProductStatus, 'draft')]:
            record = model.objects.get(code=code)
            self.assertEqual(self.client.delete(reverse(f'inventory:{route}-detail', args=[record.pk])).status_code, 409)

    def test_delete_and_edit_actions_can_coexist(self):
        for action in ['view', 'change', 'delete']:
            self.grant(self.product, action)
        response = self.client.get(reverse('inventory_pages:product-list'))
        self.assertContains(response, 'data-edit-url-template="/inventory/products/0/edit/"')
        self.assertContains(response, 'data-delete-url-template="/inventory/api/products/0/"')
        self.assertEqual(sum(column['key'] == 'actions' for column in response.context['columns']), 1)
