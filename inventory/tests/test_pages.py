from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client, TestCase
from django.urls import reverse

from ..catalog import RESOURCES
from ..models import Product, UnitOfMeasure


class InventoryPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='inventory-pages@example.com')
        cls.unit = UnitOfMeasure.objects.create(name='Pieces', code='PCS')

    def setUp(self):
        self.client.force_login(self.user)

    def grant(self, resource, *actions):
        self.user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label=resource.model._meta.app_label,
            codename__in=[f'{action}_{resource.model._meta.model_name}' for action in actions],
        ))

    def test_every_page_requires_login_and_model_permissions(self):
        anonymous = Client()
        for resource in RESOURCES:
            with self.subTest(resource=resource.key):
                listing = reverse(f'inventory_pages:{resource.key}-list')
                adding = reverse(f'inventory_pages:add-{resource.key}')
                for url in [listing, adding]:
                    self.assertRedirects(anonymous.get(url), reverse('account_pages:login'))
                    self.assertEqual(self.client.get(url).status_code, 403)
                self.grant(resource, 'view')
                response = self.client.get(listing)
                self.assertEqual(response.status_code, 200)
                self.assertIn('no-store', response['Cache-Control'])
                self.assertContains(response, reverse(f'inventory:{resource.key}-list-view'))
                self.assertNotContains(response, f'Add {resource.singular}</a>')
                self.assertEqual(self.client.get(adding).status_code, 403)
                self.grant(resource, 'add')
                self.assertContains(self.client.get(listing), f'Add {resource.singular}</a>')
                response = self.client.get(adding)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'action="{reverse(f"inventory:{resource.key}-list")}"')
                self.assertContains(response, f'data-success-url="{listing}"')
                self.assertContains(response, 'name="csrfmiddlewaretoken"')
                self.assertIn('no-store', response['Cache-Control'])

    def test_add_permission_alone_does_not_expose_unreadable_destination(self):
        for resource in RESOURCES:
            self.grant(resource, 'add')
            self.assertEqual(self.client.get(reverse(f'inventory_pages:add-{resource.key}')).status_code, 403)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'data-inventory-toggle')

    def test_sidebar_products_first_and_only_product_has_add_shortcut(self):
        for resource in RESOURCES:
            self.grant(resource, 'view', 'add')
        response = self.client.get(reverse('inventory_pages:product-list'))
        html = response.content.decode()
        submenu = html.split('<div id="inventory-submenu"', 1)[1].split('</div>', 1)[0]
        previous = -1
        for resource in RESOURCES:
            position = submenu.index(f'href="{reverse(f"inventory_pages:{resource.key}-list")}"')
            self.assertGreater(position, previous)
            previous = position
            adding = reverse(f'inventory_pages:add-{resource.key}')
            if resource.key == 'product':
                self.assertIn(f'href="{adding}"', submenu)
            else:
                self.assertNotIn(f'href="{adding}"', submenu)
        self.assertContains(response, 'aria-controls="inventory-submenu" aria-expanded="true"')
        self.assertIn('aria-current="page">Product list', submenu)
        adding = self.client.get(reverse('inventory_pages:add-product'))
        self.assertContains(adding, 'aria-current="page">Add product')

    def test_group_permissions_and_revocation_apply_to_pages_and_navigation(self):
        role = Group.objects.create(name='Catalog operators')
        role.permissions.add(*Permission.objects.filter(content_type__app_label='inventory',
                                                        codename__in=['view_product', 'add_product']))
        self.user.groups.add(role)
        url = reverse('inventory_pages:add-product')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Product list')
        self.assertNotContains(response, 'Category list')
        role.permissions.clear()
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'data-inventory-toggle')

    def test_product_page_renders_html_fields_and_loads_options_through_api(self):
        self.grant(RESOURCES[0], 'view', 'add')
        response = self.client.get(reverse('inventory_pages:add-product'))
        self.assertNotIn('form', response.context)
        for name in ['sku', 'unit', 'product_type', 'status', 'image', 'attributes', 'Whole_sale_price']:
            self.assertContains(response, f'name="{name}"')
        self.assertContains(response, 'data-options-url="/inventory/api/products/options/"')
        self.assertContains(response, 'data-default="goods"')
        self.assertContains(response, 'data-default="draft"')
        self.assertContains(response, 'accept="image/*"')
        self.assertContains(response, 'data-json-field="true"')
        self.assertNotContains(response, 'name="create_by"')
        self.assertNotContains(response, 'name="track_inventory"')
        self.assertNotContains(response, '<option value="BDT"')

    def test_product_save_starts_disabled_until_options_api_loads(self):
        UnitOfMeasure.objects.all().delete()
        self.grant(RESOURCES[0], 'view', 'add')
        response = self.client.get(reverse('inventory_pages:add-product'))
        self.assertContains(response, 'data-catalog-submit disabled')
        self.assertNotIn('missing_references', response.context)
        self.assertEqual(self.client.get(reverse('inventory:product-options')).json()['unit'], [])

    def test_forms_submit_to_existing_csrf_protected_apis_and_records_appear_in_lists(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        payloads = {
            'category': {'name': 'Office supplies', 'code': 'OFFICE', 'is_active': True},
            'brand': {'name': 'Sample brand', 'is_active': False},
            'unit': {'name': 'Boxes', 'code': 'BOX', 'is_active': True},
            'product-type': {'name': 'Rental', 'code': 'rental', 'is_active': True},
            'status': {'name': 'Review', 'code': 'review', 'is_active': True},
            'currency': {'name': 'US dollar', 'code': 'USD', 'exchange_rate': '120', 'is_active': True},
            'product': {'name': 'Sample product', 'sku': 'PAGE-001', 'currency': 'BDT',
                        'unit': self.unit.pk, 'product_type': 'goods', 'status': 'draft',
                        'sale_price': '10.0000', 'attributes': '{"color":"blue"}', 'is_returnable': 'false'},
        }
        for resource in RESOURCES:
            with self.subTest(resource=resource.key):
                self.grant(resource, 'view', 'add')
                page = client.get(reverse(f'inventory_pages:add-{resource.key}'))
                create_url = page.context['create_api_url']
                payload = payloads[resource.key]
                self.assertEqual(client.post(create_url, payload).status_code, 403)
                token = client.cookies['csrftoken'].value
                kwargs = {'HTTP_X_CSRFTOKEN': token}
                if resource.key != 'product':
                    kwargs['content_type'] = 'application/json'
                response = client.post(create_url, payload, **kwargs)
                self.assertEqual(response.status_code, 201, response.content)
                record = resource.model.objects.get(name=payload['name'])
                self.assertEqual(record.create_by, self.user.username)
                listing = client.get(page.context['list_url'])
                self.assertEqual(listing.status_code, 200)
                results = client.get(listing.context['list_api_url'], {'q': payload['name']}).json()
                self.assertEqual(results['count'], 1)
                self.assertEqual(results['results'][0]['id'], record.pk)
        self.assertEqual(Product.objects.get(sku='PAGE-001').attributes, {'color': 'blue'})
