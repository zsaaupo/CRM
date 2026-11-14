from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client, TestCase
from django.urls import reverse

from inventory.models import Brand, Product, ProductCategory, UnitOfMeasure
from inventory.serializers import ProductDetailSerializer


class ProductDetailPageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='product-reader')
        self.client.force_login(self.user)
        unit = UnitOfMeasure.objects.create(name='Pieces', code='PCS')
        self.base = Product.objects.create(name='Base keyboard', sku='BASE', unit=unit, currency_id='BDT')
        self.product = Product.objects.create(
            name='Blue keyboard', sku='BLUE', unit=unit, currency_id='BDT', variant_of=self.base,
            category=ProductCategory.objects.create(name='Accessories', code='ACC'),
            brand=Brand.objects.create(name='Sample brand'), description='<script>unsafe</script>',
            attributes={'color': 'blue'}, sale_price='20', minimum_sale_price='0',
            Whole_sale_price=None, is_returnable=False, image='products_image/keyboard.png',
        )
        self.url = reverse('inventory_pages:product-detail', args=[self.product.pk])
        self.api_url = reverse('inventory:product-detail', args=[self.product.pk])

    def grant(self, *actions):
        self.user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='inventory', codename__in=[f'{action}_product' for action in actions],
        ))

    def test_view_permission_allows_details_and_list_action_without_write_permissions(self):
        self.assertRedirects(Client().get(self.url), reverse('account_pages:login'))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(self.api_url).status_code, 403)
        self.grant('view')
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertIn('no-store', page['Cache-Control'])
        self.assertContains(page, f'data-api-url="{self.api_url}"')
        self.assertContains(page, '/static/inventory/js/product_detail.js')
        self.assertContains(page, 'data-record-content hidden')
        self.assertContains(page, 'aria-current="page">Product list')
        self.assertNotIn('edit_url', page.context)
        self.assertNotContains(page, self.product.name)
        for field in ['sku', 'category_name', 'brand_name', 'unit_name', 'attributes', 'Whole_sale_price', 'create_by']:
            self.assertContains(page, f'data-detail-field="{field}"')
        listing = self.client.get(reverse('inventory_pages:product-list'))
        self.assertContains(listing, 'data-view-url-template="/inventory/products/0/"')
        self.assertEqual(sum(column['key'] == 'actions' for column in listing.context['columns']), 1)
        self.assertNotIn('edit_url_template', listing.context)
        self.assertNotIn('delete_url_template', listing.context)
        self.grant('change', 'delete')
        listing = self.client.get(reverse('inventory_pages:product-list'))
        self.assertEqual(sum(column['key'] == 'actions' for column in listing.context['columns']), 1)
        self.assertContains(listing, 'data-edit-url-template="/inventory/products/0/edit/"')
        self.assertContains(self.client.get(self.url), f'href="{reverse("inventory_pages:edit-product", args=[self.product.pk])}"')

    def test_detail_api_has_read_only_labels_nullable_fields_and_image(self):
        self.grant('view')
        data = self.client.get(self.api_url).json()
        expected = {'name': 'Blue keyboard', 'product_type_name': 'Physical goods',
                    'status_name': 'Draft', 'category_name': 'Accessories', 'brand_name': 'Sample brand',
                    'unit_name': 'Pieces', 'unit_code': 'PCS', 'variant_of_name': 'Base keyboard',
                    'variant_of_sku': 'BASE', 'is_returnable': False, 'minimum_sale_price': '0.0000',
                    'Whole_sale_price': None, 'attributes': {'color': 'blue'}}
        # The product type label is administrator-editable; check against its current value.
        expected['product_type_name'] = self.product.product_type.name
        for name, value in expected.items():
            self.assertEqual(data[name], value)
        self.assertTrue(data['image'].endswith('/products_image/keyboard.png'))
        empty = self.client.get(reverse('inventory:product-detail', args=[self.base.pk])).json()
        for name in ['brand_name', 'category_name', 'variant_of_name', 'variant_of_sku']:
            self.assertIsNone(empty[name])
        self.assertIsNone(empty['image'])
        self.grant('change')
        page = self.client.get(self.url)
        response = self.client.patch(self.api_url, {'brand_name': 'Spoof', 'unit_code': 'BAD'},
                                     content_type='application/json', HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['brand_name'], 'Sample brand')
        self.assertEqual(response.json()['unit_code'], 'PCS')

    def test_related_labels_do_not_add_queries_and_group_revocation_blocks_access(self):
        with self.assertNumQueries(1):
            data = ProductDetailSerializer(Product.objects.select_related(
                'product_type', 'status', 'category', 'brand', 'unit', 'variant_of', 'currency',
            ).get(pk=self.product.pk)).data
        self.assertEqual(data['unit_name'], 'Pieces')
        role = Group.objects.create(name='Product readers')
        role.permissions.add(Permission.objects.get(content_type__app_label='inventory', codename='view_product'))
        self.user.groups.add(role)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.get(reverse('inventory_pages:product-detail', args=[999999])).status_code, 200)
        self.assertEqual(self.client.get(reverse('inventory:product-detail', args=[999999])).status_code, 404)
        role.permissions.clear()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(self.api_url).status_code, 403)
