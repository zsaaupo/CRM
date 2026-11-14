from common.models import Status
from html.parser import HTMLParser
from io import BytesIO
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image
from rest_framework.test import APIClient

from ..models import Brand, Currency, Product, ProductCategory, ProductType, UnitOfMeasure


class InputAttributes(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.inputs = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('name'):
            self.inputs[attrs['name']] = attrs


class InventoryEditPageTests(TestCase):
    def setUp(self):
        self.enterContext(override_settings(MEDIA_ROOT=self.enterContext(TemporaryDirectory())))
        self.user = get_user_model().objects.create_user(username='page-editor')
        unit = UnitOfMeasure.objects.create(name='Pieces', code='PCS')
        category = ProductCategory.objects.create(name='Existing category', code='CAT')
        brand = Brand.objects.create(name='Existing brand')
        self.product = Product.objects.create(name='Existing product', sku='EDIT-001', currency_id='BDT',
                                              unit=unit, category=category, brand=brand,
                                              minimum_sale_price='5', sale_price='20', Whole_sale_price='10',
                                              image='products_image/old.png')
        self.resources = [
            ('product', self.product), ('category', category), ('brand', brand), ('unit', unit),
            ('product-type', ProductType.objects.create(name='Rental', code='rental')),
            ('status', Status.objects.create(name='Review', code='review')),
            ('currency', Currency.objects.create(name='US dollar', code='USD', exchange_rate='120')),
        ]
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)

    def grant(self, record, *actions):
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label=record._meta.app_label,
            codename__in=[f'{action}_{record._meta.model_name}' for action in actions]))

    def test_each_list_edit_action_page_and_api_save_destination(self):
        for route, record in self.resources:
            with self.subTest(route=route):
                listing = reverse(f'inventory_pages:{route}-list')
                edit = reverse(f'inventory_pages:edit-{route}', args=[record.pk])
                self.assertRedirects(Client().get(edit), reverse('account_pages:login'))
                self.assertEqual(self.client.get(edit).status_code, 403)
                self.grant(record, 'view')
                self.assertNotIn('edit_url_template', self.client.get(listing).context)
                self.assertEqual(self.client.get(edit).status_code, 403)
                self.grant(record, 'change')
                response = self.client.get(listing)
                self.assertContains(response, '"type": "edit"')
                self.assertContains(response, reverse(f'inventory_pages:edit-{route}', args=[0]))
                page = self.client.get(edit)
                self.assertEqual(page.status_code, 200)
                self.assertIn('no-store', page['Cache-Control'])
                self.assertNotIn('form', page.context)
                self.assertContains(page, 'data-method="PATCH"')
                self.assertContains(page, 'data-catalog-submit disabled')
                self.assertContains(page, f'data-success-url="{listing}"')
                detail = reverse(f'inventory:{route}-detail', args=[record.pk])
                update = reverse(f'inventory:{route}-update', args=[record.pk])
                self.assertContains(page, f'data-detail-url="{detail}"')
                self.assertContains(page, f'action="{update}"')
                self.assertEqual(self.client.get(detail).json()['name'], record.name)
                if route in ['product-type', 'status', 'currency', 'category', 'unit']:
                    inputs = InputAttributes(page.content.decode()).inputs
                    self.assertEqual('disabled' in inputs['code'], route in ['product-type', 'status', 'currency'])
                self.client.credentials(HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)
                result = self.client.patch(update, {'name': f'Edited {route}'}, format='json')
                self.assertEqual(result.status_code, 200, result.data)
                results = self.client.get(reverse(f'inventory:{route}-list-view'), {'q': f'Edited {route}'}).json()
                self.assertEqual(results['count'], 1)
                self.assertEqual(results['results'][0]['id'], record.pk)

    def test_product_edit_options_need_view_and_change_but_not_add_permission(self):
        url = reverse('inventory:product-edit-options', args=[self.product.pk])
        self.grant(self.product, 'change')
        self.assertEqual(self.client.get(url).status_code, 403)
        self.grant(self.product, 'view')
        self.assertEqual(self.client.get(reverse('inventory:product-options')).status_code, 403)
        page = self.client.get(reverse('inventory_pages:edit-product', args=[self.product.pk]))
        self.assertContains(page, f'data-options-url="{url}"')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['variant_of'], [])
        self.assertEqual(self.client.get(reverse('inventory:product-edit-options', args=[9999])).status_code, 404)
        base = Product.objects.create(name='Base', sku='BASE', unit=self.product.unit, currency_id='BDT')
        options = self.client.get(url).json()['variant_of']
        self.assertEqual(options, [{'value': base.pk, 'label': 'BASE — Base'}])
        self.assertEqual(self.client.get(reverse('inventory:product-edit-options', args=[base.pk])).status_code, 200)
        self.product.variant_of = base
        self.product.save()
        self.assertEqual(self.client.get(reverse('inventory:product-edit-options', args=[base.pk])).json()['variant_of'], [])

    def test_product_patch_keeps_existing_image_and_multipart_clears_optional_fields(self):
        self.grant(self.product, 'view', 'change')
        self.client.get(reverse('inventory_pages:edit-product', args=[self.product.pk]))
        self.client.credentials(HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)
        url = reverse('inventory:product-update', args=[self.product.pk])
        response = self.client.patch(url, {'name': 'Kept image', 'category': None}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['image'].endswith('/products_image/old.png'))
        base = Product.objects.create(name='Base', sku='BASE', unit=self.product.unit, currency_id='BDT')
        self.product.variant_of = base
        self.product.save()
        stream = BytesIO()
        Image.new('RGB', (2, 2), 'green').save(stream, 'PNG')
        file = SimpleUploadedFile('replacement.png', stream.getvalue(), content_type='image/png')
        response = self.client.patch(url, {'image': file, 'category': '', 'brand': '', 'variant_of': '',
                                          'minimum_sale_price': '', 'Whole_sale_price': '',
                                          'attributes': '{"color":"green"}', 'is_returnable': 'false'}, format='multipart')
        self.assertEqual(response.status_code, 200, response.data)
        for field in ['category', 'brand', 'variant_of', 'minimum_sale_price', 'Whole_sale_price']:
            self.assertIsNone(response.data[field])
        self.assertTrue(response.data['image'].endswith('/products_image/replacement.png'))
        self.assertEqual(response.data['attributes'], {'color': 'green'})
        self.assertFalse(response.data['is_returnable'])

    def test_group_edit_grants_and_revocation_apply_to_actions_and_pages(self):
        role = Group.objects.create(name='Product editors')
        role.permissions.add(*Permission.objects.filter(content_type__app_label='inventory',
                                                        codename__in=['view_product', 'change_product']))
        self.user.groups.add(role)
        url = reverse('inventory_pages:edit-product', args=[self.product.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        role.permissions.remove(Permission.objects.get(content_type__app_label='inventory', codename='change_product'))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertNotIn('edit_url_template', self.client.get(reverse('inventory_pages:product-list')).context)
