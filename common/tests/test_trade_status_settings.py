from html.parser import HTMLParser
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from common.models import ProductStatus, TradeStatus
from inventory.models import Product, UnitOfMeasure
from purchase.models import PurchaseOrder
from sale.models import SalesOrder


class SettingsInputs(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.fields = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('input', 'textarea') and attrs.get('name'):
            self.fields[attrs['name']] = attrs


class TradeStatusSettingsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='trade-status-manager')
        self.record = TradeStatus.objects.create(code='custom-review', name='Custom review',
                                                 create_by='original', modified_by='original')
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.list_url = reverse('common_pages:trade-status-list')
        self.add_url = reverse('common_pages:add-trade-status')
        self.edit_url = reverse('common_pages:edit-trade-status', args=[self.record.pk])
        self.api_url = reverse('common:trade-status-list')
        self.detail = reverse('common:trade-status-detail', args=[self.record.pk])
        self.update = reverse('common:trade-status-update', args=[self.record.pk])
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)

    def grant(self, *actions):
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label='common',
                                       codename__in=[f'{action}_tradestatus' for action in actions]))

    def test_navigation_and_pages_require_trade_view_and_action_permissions(self):
        self.assertNotContains(self.client.get(reverse('dashboard')), 'data-settings-toggle')
        for url in [self.list_url, self.add_url, self.edit_url]:
            self.assertRedirects(Client().get(url), reverse('account_pages:login'))
            self.assertEqual(self.client.get(url).status_code, 403)
        self.grant('add')
        self.assertEqual(self.client.get(self.add_url).status_code, 403)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'data-settings-toggle')
        self.grant('view')
        dashboard = self.client.get(reverse('dashboard'))
        self.assertContains(dashboard, 'data-settings-toggle')
        self.assertContains(dashboard, f'href="{self.list_url}"')
        self.assertContains(dashboard, 'Trade status setting')
        self.assertEqual(self.client.get(self.edit_url).status_code, 403)
        self.grant('change')
        for url in [self.list_url, self.add_url, self.edit_url]:
            page = self.client.get(url)
            self.assertEqual(page.status_code, 200)
            self.assertIn('no-store', page['Cache-Control'])
            self.assertContains(page, 'aria-controls="settings-submenu" aria-expanded="true"')
            self.assertContains(page, 'aria-current="page">Trade status setting')

    def test_list_matches_catalog_structure_and_gates_edit_delete_buttons(self):
        self.grant('view')
        response = self.client.get(self.list_url)
        self.assertContains(response, 'data-api-url="/common/api/trade-statuses/list/"')
        self.assertContains(response, '/static/inventory/js/catalog_list.js')
        self.assertNotContains(response, f'href="{self.add_url}"')
        self.assertNotIn('edit_url_template', response.context)
        self.assertNotIn('delete_url_template', response.context)
        self.assertNotContains(response, 'data-catalog-delete-dialog')
        self.grant('add', 'change', 'delete')
        response = self.client.get(self.list_url)
        self.assertContains(response, f'href="{self.add_url}"')
        self.assertContains(response, 'data-edit-url-template="/settings/trade-statuses/0/edit/"')
        self.assertContains(response, 'data-delete-url-template="/common/api/trade-statuses/0/"')
        self.assertContains(response, 'data-catalog-delete-dialog')
        self.assertEqual(sum(column['key'] == 'actions' for column in response.context['columns']), 1)

    def test_forms_have_same_fields_api_controller_and_success_destination(self):
        self.grant('view', 'add', 'change')
        adding = self.client.get(self.add_url)
        fields = SettingsInputs(adding.content.decode()).fields
        self.assertEqual(set(fields) - {'csrfmiddlewaretoken'}, {'name', 'code', 'description', 'is_active'})
        self.assertNotIn('form', adding.context)
        self.assertContains(adding, f'action="{self.api_url}"')
        self.assertContains(adding, '/static/inventory/js/catalog_form.js')
        self.assertContains(adding, f'data-success-url="{self.list_url}"')
        self.assertIn('required', fields['name'])
        self.assertEqual(fields['code']['maxlength'], '32')
        self.assertNotIn('disabled', fields['code'])
        editing = self.client.get(self.edit_url)
        self.assertIn('disabled', SettingsInputs(editing.content.decode()).fields['code'])
        self.assertContains(editing, f'action="{self.update}"')
        self.assertContains(editing, f'data-detail-url="{self.detail}"')
        self.assertContains(editing, 'data-method="PATCH"')
        self.assertContains(editing, 'data-catalog-submit disabled')
        self.assertContains(editing, f'data-success-url="{self.list_url}"')

    def test_page_api_crud_roundtrip_preserves_audit_and_shared_proxy_records(self):
        self.grant('view', 'add', 'change', 'delete')
        adding = self.client.get(self.add_url)
        response = self.client.post(adding.context['create_api_url'], {
            'name': 'Ready for review', 'code': 'ready-review', 'description': 'Shared label',
            'is_active': True, 'create_by': 'spoof', 'modified_by': 'spoof',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        data = response.data['trade_status']
        self.assertEqual(data['create_by'], self.user.username)
        self.assertEqual(data['modified_by'], self.user.username)
        self.assertEqual(ProductStatus.objects.get(pk=data['id']).name, data['name'])
        editing = self.client.get(reverse('common_pages:edit-trade-status', args=[data['id']]))
        self.assertEqual(self.client.get(editing.context['detail_api_url']).data['name'], data['name'])
        response = self.client.patch(editing.context['create_api_url'], {'name': 'Reviewed', 'is_active': False,
                                     'create_by': 'spoof'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['create_by'], self.user.username)
        self.assertEqual(ProductStatus.objects.get(pk=data['id']).name, 'Reviewed')
        self.assertEqual(self.client.get(self.client.get(self.list_url).context['list_api_url'], {'q': 'Reviewed'}).data['count'], 1)
        detail = reverse('common:trade-status-detail', args=[data['id']])
        self.assertEqual(self.client.delete(detail).status_code, 204)
        self.assertFalse(ProductStatus.objects.filter(pk=data['id']).exists())
        self.assertEqual(self.client.get(detail).status_code, 404)

    def test_validation_csrf_duplicate_codes_and_database_errors(self):
        self.grant('view', 'add', 'change', 'delete')
        for changes in [{'name': ''}, {'name': 'N' * 101}, {'code': 'bad code'}, {'code': 'C' * 33},
                        {'code': self.record.code}]:
            response = self.client.post(self.api_url, {'name': 'Valid', 'code': 'valid', **changes}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.client.patch(self.update, {'code': 'changed-code'}, format='json').status_code, 400)
        with patch('common.serializers.TradeStatusSerializer.save', side_effect=IntegrityError):
            self.assertEqual(self.client.patch(self.update, {'name': 'Failed'}, format='json').status_code, 400)
        self.record.refresh_from_db()
        self.assertEqual(self.record.name, 'Custom review')
        self.client.credentials()
        for method, url in [('post', self.api_url), ('patch', self.update), ('put', self.detail), ('delete', self.detail)]:
            self.assertEqual(getattr(self.client, method)(url, {'name': 'Denied'}, format='json').status_code, 403)

    def test_default_draft_and_referenced_product_purchase_sale_statuses_are_protected(self):
        self.grant('delete')
        draft = TradeStatus.objects.get(code='draft')
        self.assertEqual(self.client.delete(reverse('common:trade-status-detail', args=[draft.pk])).status_code, 409)
        unit = UnitOfMeasure.objects.create(name='Pieces', code='TRADE-PCS')
        for index, model in enumerate([Product, PurchaseOrder, SalesOrder]):
            status = TradeStatus.objects.create(code=f'linked-{index}', name=f'Linked {index}')
            fields = {'currency_id': 'BDT', 'status_id': status.code}
            if model is Product:
                fields.update(name='Product', sku='TRADE-STATUS', unit=unit)
            else:
                fields['exchange_rate'] = '1'
            model.objects.create(**fields)
            response = self.client.delete(reverse('common:trade-status-detail', args=[status.pk]))
            self.assertEqual(response.status_code, 409, response.data)
            self.assertTrue(TradeStatus.objects.filter(pk=status.pk).exists())

    def test_search_pagination_methods_and_change_only_update_access(self):
        self.grant('view')
        TradeStatus.objects.bulk_create([TradeStatus(code=f'settings-{i}', name=f'Settings {i:02}') for i in range(25)])
        url = reverse('common:trade-status-list-view')
        first = self.client.get(url, {'q': 'Settings'}).data
        self.assertEqual(first['count'], 25)
        self.assertEqual(len(first['results']), 20)
        self.assertEqual(first['results'][0]['name'], 'Settings 00')
        self.assertEqual(len(self.client.get(first['next']).data['results']), 5)
        for method in ['post', 'put', 'patch', 'delete']:
            self.assertEqual(getattr(self.client, method)(url, {}, format='json').status_code, 405)
        self.user.user_permissions.clear()
        self.grant('change')
        self.assertEqual(self.client.patch(self.update, {'description': 'Changed'}, format='json').status_code, 200)
        self.assertEqual(self.client.options(self.update).status_code, 200)
        self.assertEqual(self.client.get(self.detail).status_code, 403)
        self.assertEqual(self.client.get(self.edit_url).status_code, 403)

    def test_group_grants_and_revocation_do_not_require_product_status_permission(self):
        group = Group.objects.create(name='Trade status readers')
        group.permissions.add(Permission.objects.get(content_type__app_label='common', codename='view_tradestatus'))
        self.user.groups.add(group)
        self.assertEqual(self.client.get(self.list_url).status_code, 200)
        self.assertEqual(self.client.get(reverse('inventory:status-list-view')).status_code, 403)
        group.permissions.clear()
        self.assertEqual(self.client.get(self.list_url).status_code, 403)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'data-settings-toggle')
