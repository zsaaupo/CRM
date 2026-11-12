from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from warehouse.models import WareHouse


class PageInputs(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.fields = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ['input', 'textarea'] and attrs.get('name'):
            self.fields[attrs['name']] = attrs


class WareHousePageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='warehouse-page-operator')
        self.record = WareHouse.objects.create(name='Existing depot', address='Dhaka', phone='01700000000')
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.list_url = reverse('warehouse_pages:warehouse-list')
        self.add_url = reverse('warehouse_pages:add-warehouse')
        self.edit_url = reverse('warehouse_pages:edit-warehouse', args=[self.record.pk])

    def grant(self, *actions):
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label='warehouse',
                                        codename__in=[f'{action}_warehouse' for action in actions]))

    def test_pages_require_login_and_separate_model_permissions(self):
        for url in [self.list_url, self.add_url, self.edit_url]:
            self.assertRedirects(Client().get(url), reverse('account_pages:login'))
            self.assertEqual(self.client.get(url).status_code, 403)
        self.grant('view')
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-api-url="/warehouse/api/warehouses/list/"')
        self.assertNotContains(response, f'href="{self.add_url}"')
        self.assertNotIn('edit_url_template', response.context)
        self.assertNotContains(response, 'data-catalog-delete-dialog')
        self.assertEqual(self.client.get(self.add_url).status_code, 403)
        self.assertEqual(self.client.get(self.edit_url).status_code, 403)
        self.grant('add', 'change', 'delete')
        for url in [self.list_url, self.add_url, self.edit_url]:
            self.assertEqual(self.client.get(url).status_code, 200)

    def test_add_page_has_html_fields_and_api_redirect_contract(self):
        self.grant('view', 'add')
        response = self.client.get(self.add_url)
        self.assertNotIn('form', response.context)
        self.assertContains(response, 'action="/warehouse/api/warehouses/"')
        self.assertContains(response, 'data-method="POST"')
        self.assertContains(response, f'data-success-url="{self.list_url}"')
        self.assertContains(response, '/static/inventory/js/catalog_form.js')
        self.assertContains(response, '<p class="page-eyebrow">Warehouses</p>')
        fields = PageInputs(response.content.decode()).fields
        self.assertEqual(set(fields) - {'csrfmiddlewaretoken'}, {'name', 'address', 'phone', 'email', 'code', 'is_active'})
        for name in ['name', 'address', 'phone']:
            self.assertIn('required', fields[name])
            self.assertEqual(fields[name]['maxlength'], '200')
        for name in ['email', 'code']:
            self.assertNotIn('required', fields[name])
        self.assertEqual(fields['email']['type'], 'email')
        self.assertIn('checked', fields['is_active'])
        self.assertIn('no-store', response['Cache-Control'])

    def test_edit_page_loads_detail_api_and_submits_dedicated_patch(self):
        self.grant('view', 'change')
        response = self.client.get(self.edit_url)
        self.assertContains(response, 'Edit warehouse')
        self.assertContains(response, 'data-method="PATCH"')
        self.assertContains(response, f'action="{reverse("warehouse:warehouse-update", args=[self.record.pk])}"')
        self.assertContains(response, f'data-detail-url="{reverse("warehouse:warehouse-detail", args=[self.record.pk])}"')
        self.assertContains(response, f'data-success-url="{self.list_url}"')
        self.assertContains(response, 'data-catalog-submit disabled')
        self.assertNotContains(response, 'value="Existing depot"')
        self.assertNotIn('disabled', PageInputs(response.content.decode()).fields['code'])
        self.assertEqual(self.client.get(reverse('warehouse_pages:edit-warehouse', args=[9999])).status_code, 200)
        self.assertEqual(self.client.get(reverse('warehouse:warehouse-detail', args=[9999])).status_code, 404)

    def test_list_actions_use_existing_edit_and_delete_controllers(self):
        self.grant('view', 'delete')
        response = self.client.get(self.list_url)
        self.assertContains(response, 'data-delete-url-template="/warehouse/api/warehouses/0/"')
        self.assertContains(response, 'data-catalog-delete-dialog')
        self.assertNotIn('edit_url_template', response.context)
        self.grant('change', 'add')
        response = self.client.get(self.list_url)
        self.assertContains(response, 'data-edit-url-template="/warehouse/warehouses/0/edit/"')
        self.assertContains(response, f'href="{self.add_url}"')
        self.assertEqual(sum(column['key'] == 'actions' for column in response.context['columns']), 1)
        self.assertContains(response, '/static/inventory/js/catalog_list.js')

    def test_warehouse_navigation_appears_across_site_with_active_page_state(self):
        self.assertNotContains(self.client.get(reverse('dashboard')), 'data-warehouses-toggle')
        self.grant('add')
        self.assertNotContains(self.client.get(reverse('dashboard')), 'data-warehouses-toggle')
        self.assertEqual(self.client.get(self.add_url).status_code, 403)
        self.grant('view', 'change')
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'data-warehouses-toggle')
        self.assertContains(response, f'href="{self.list_url}"')
        self.assertContains(response, f'href="{self.add_url}"')
        for url in [self.list_url, self.edit_url]:
            response = self.client.get(url)
            self.assertContains(response, 'aria-controls="warehouses-submenu" aria-expanded="true"')
            self.assertContains(response, 'aria-current="page">Warehouse list')
        self.assertContains(self.client.get(self.add_url), 'aria-current="page">Add warehouse')

    def test_api_create_edit_delete_roundtrip_using_page_urls_and_csrf(self):
        self.grant('view', 'add', 'change', 'delete')
        page = self.client.get(self.add_url)
        token = self.client.cookies['csrftoken'].value
        data = {'name': 'New depot', 'address': 'Gazipur', 'phone': '01800000000', 'is_active': True}
        self.assertEqual(self.client.post(page.context['create_api_url'], data, format='json').status_code, 403)
        self.client.credentials(HTTP_X_CSRFTOKEN=token)
        response = self.client.post(page.context['create_api_url'], data, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        record = response.data['warehouse']
        edit = self.client.get(reverse('warehouse_pages:edit-warehouse', args=[record['id']]))
        self.assertEqual(self.client.get(edit.context['detail_api_url']).data['name'], 'New depot')
        response = self.client.patch(edit.context['create_api_url'], {'name': 'Edited depot', 'is_active': False}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        listing = self.client.get(edit.context['list_url'])
        records = self.client.get(listing.context['list_api_url'], {'q': 'Edited depot'}).data
        self.assertEqual(records['count'], 1)
        self.assertFalse(records['results'][0]['is_active'])
        delete_url = listing.context['delete_url_template'].replace('/0/', f'/{record["id"]}/')
        self.assertEqual(self.client.delete(delete_url).status_code, 204)
        self.assertFalse(WareHouse.objects.filter(pk=record['id']).exists())

    def test_group_grants_and_revocation_apply_to_page_and_menu(self):
        role = Group.objects.create(name='Warehouse editors')
        role.permissions.add(*Permission.objects.filter(content_type__app_label='warehouse',
                                    codename__in=['view_warehouse', 'change_warehouse']))
        self.user.groups.add(role)
        self.assertEqual(self.client.get(self.edit_url).status_code, 200)
        role.permissions.clear()
        self.assertEqual(self.client.get(self.edit_url).status_code, 403)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'data-warehouses-toggle')
