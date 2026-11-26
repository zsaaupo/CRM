from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from rest_framework.test import APITestCase

from audit_log.models import AuditLog
from audit_log.service import visible_logs
from inventory.models import RAW_MATERIAL_TYPE, Product, ProductCategory, UnitOfMeasure
from people.models import Party
from warehouse.models import Stock, WareHouse
from warehouse.tests.helpers import assign_warehouse_role

from .models import RawMaterial


class RawMaterialCRUDTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.main = WareHouse.objects.create(name='Main')
        cls.branch = WareHouse.objects.create(name='Branch')
        cls.unit = UnitOfMeasure.objects.create(name='Kilogram', code='KG')
        cls.user = get_user_model().objects.create_user(username='material-clerk')
        cls.scope = assign_warehouse_role(cls.user, warehouse=cls.main)
        cls.permissions = Permission.objects.filter(content_type__app_label='raw_material')
        cls.scope.role.permissions.set(cls.permissions)

    def setUp(self):
        self.client.force_login(self.user)
        self.url = reverse('raw_material:material-list')
        self.payload = {'sku': 'RM-0001', 'name': 'Steel', 'unit': self.unit.pk, 'currency': 'BDT',
                        'cost_price': '92.50', 'reorder_level': '25.500', 'attributes': {'grade': 'SPCC'}}

    def create_material(self):
        response = self.client.post(self.url, self.payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['raw_material']

    def test_crud_uses_existing_product_stock_and_branch_scoped_audit(self):
        record = self.create_material()
        pk = record['id']
        detail = reverse('raw_material:material-detail', args=[pk])
        self.assertEqual(record['warehouse'], self.main.pk)
        self.assertEqual(record['product_type'], RAW_MATERIAL_TYPE)
        self.assertEqual(record['quantity'], '0.000000')
        self.assertEqual(record['create_by'], self.user.username)
        self.assertEqual(RawMaterial.objects.get(pk=pk).create_by, self.user.username)
        Stock.objects.create(product_id=pk, warehouse=self.main, quantity='22.57')
        Stock.objects.create(product_id=pk, warehouse=self.branch, quantity='500')
        response = self.client.get(self.url, {'q': '0001'})
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['quantity'], '22.570000')
        self.assertTrue(response.data['results'][0]['low_stock'])

        update = reverse('raw_material:material-update', args=[pk])
        response = self.client.patch(update, {'name': 'Steel sheet', 'reorder_level': None}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data['reorder_level'])
        self.assertFalse(response.data['low_stock'])
        self.assertEqual(RawMaterial.objects.get(pk=pk).modified_by, self.user.username)
        self.assertEqual(self.client.delete(detail).status_code, 409)
        Stock.objects.filter(product_id=pk).delete()
        self.assertEqual(self.client.delete(detail).status_code, 204)
        self.assertFalse(Product.objects.filter(pk=pk).exists())
        events = AuditLog.objects.filter(object_id=str(pk), reference='RM-0001')
        self.assertEqual(set(events.values_list('resource', flat=True)), {'raw_material.rawmaterial'})
        self.assertEqual(set(events.values_list('action', flat=True)), {'created', 'updated', 'deleted'})
        self.assertEqual(visible_logs(events, self.user).count(), 3)

    def test_branch_scope_applies_to_pages_choices_and_direct_writes(self):
        record = self.create_material()
        pk = record['id']
        detail = reverse('raw_material:material-detail', args=[pk])
        detail_page = reverse('raw_material_pages:material-detail', args=[pk])
        edit = reverse('raw_material_pages:edit-material', args=[pk])
        options = reverse('raw_material:material-options')
        list_page = reverse('raw_material_pages:material-list')
        response = self.client.get(list_page)
        self.assertContains(response, 'Raw material list')
        self.assertContains(response, 'Add raw material')
        response = self.client.get(detail_page)
        self.assertContains(response, f'data-api-url="{detail}"')
        self.assertContains(response, f'href="{edit}"')
        self.assertContains(self.client.get(edit), 'data-locked="true"')
        self.assertEqual(self.client.get(options).data['warehouse'], [{'value': self.main.pk, 'label': 'Main'}])
        self.assertEqual(self.client.post(self.url, {**self.payload, 'sku': 'RM-X', 'warehouse': self.branch.pk},
                                          format='json').status_code, 403)
        self.assertEqual(self.client.patch(detail, {'warehouse': self.branch.pk}, format='json').status_code, 403)

        self.scope.warehouse = self.branch
        self.scope.save()
        self.assertEqual(self.client.get(self.url, {'q': 'Steel'}).data['count'], 0)
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertEqual(self.client.get(detail_page).status_code, 404)
        self.assertEqual(self.client.patch(detail, {'name': 'Hidden'}, format='json').status_code, 404)
        self.assertEqual(self.client.delete(detail).status_code, 404)
        self.assertEqual(self.client.get(edit).status_code, 404)
        self.assertEqual(self.client.get(reverse('raw_material:material-edit-options', args=[pk])).status_code, 404)
        self.assertFalse(visible_logs(AuditLog.objects.filter(resource='raw_material.rawmaterial'), self.user).exists())

        self.scope.warehouse = None
        self.scope.save()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(list_page).status_code, 403)
        self.assertEqual(self.client.get(detail_page).status_code, 403)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'raw-material-submenu')

    def test_view_only_role_gets_product_style_view_action_and_material_details(self):
        self.payload['category'] = ProductCategory.objects.create(name='Metals', code='METALS').pk
        self.payload['supplier'] = Party.objects.create(name='Steel supplier', code='STEEL', is_supplier=True).pk
        record = self.create_material()
        self.scope.role.permissions.set([self.permissions.get(codename='view_rawmaterial')])
        details_template = reverse('raw_material_pages:material-detail', args=[0])
        listing = self.client.get(reverse('raw_material_pages:material-list'))
        self.assertContains(listing, f'data-view-url-template="{details_template}"')
        self.assertEqual(listing.context['columns'][-1]['key'], 'actions')
        self.assertNotIn('edit_url_template', listing.context)
        self.assertNotIn('delete_url_template', listing.context)

        page = self.client.get(reverse('raw_material_pages:material-detail', args=[record['id']]))
        self.assertContains(page, 'Warehouse stock')
        self.assertContains(page, 'raw_material/js/material_detail.js')
        self.assertNotContains(page, 'Edit raw material')
        data = self.client.get(reverse('raw_material:material-detail', args=[record['id']])).data
        self.assertEqual(data['category_name'], 'Metals')
        self.assertEqual(data['supplier_name'], 'Steel supplier')
        self.assertEqual(data['unit_name'], 'Kilogram')
        self.assertEqual(data['quantity'], '0.000000')

    def test_all_branch_selection_and_standard_catalog_cannot_bypass_raw_material_permissions(self):
        self.scope.warehouse = None
        self.scope.all_warehouses = True
        self.scope.save()
        self.assertEqual(self.client.post(self.url, self.payload, format='json').status_code, 400)
        self.payload['warehouse'] = self.main.pk
        record = self.create_material()
        detail = reverse('raw_material:material-detail', args=[record['id']])
        self.branch.is_active = False
        self.branch.save(update_fields=['is_active'])
        self.assertEqual(self.client.patch(detail, {'warehouse': self.branch.pk}, format='json').status_code, 400)
        self.branch.is_active = True
        self.branch.save(update_fields=['is_active'])
        self.assertContains(self.client.get(reverse('raw_material_pages:edit-material', args=[record['id']])),
                            'Use a warehouse transfer to move existing stock.')
        Stock.objects.create(warehouse=self.main, product_id=record['id'], quantity='10')
        response = self.client.patch(detail, {'warehouse': self.branch.pk, 'sku': record['sku']}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['warehouse'], self.branch.pk)
        self.assertEqual(response.data['quantity'], '0.000000')
        self.assertEqual(Stock.objects.get(warehouse=self.main, product_id=record['id']).quantity, 10)
        self.scope.role.permissions.set([self.permissions.get(codename='view_rawmaterial')])
        self.user.user_permissions.add(self.permissions.get(codename='add_rawmaterial'))
        response = self.client.get(reverse('raw_material_pages:material-list'))
        self.assertContains(response, 'Raw material list')
        self.assertNotContains(response, 'Add raw material')
        self.assertEqual(self.client.post(self.url, self.payload, format='json').status_code, 403)

        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label='inventory',
                                                                    content_type__model='product'))
        product_url = reverse('inventory:product-detail', args=[record['id']])
        self.assertEqual(self.client.get(product_url).status_code, 404)
        self.assertEqual(self.client.patch(product_url, {'name': 'Bypass'}, format='json').status_code, 404)
        product_list = reverse('inventory:product-list')
        payload = {'name': 'Bypass', 'sku': 'BYPASS', 'currency': 'BDT', 'unit': self.unit.pk,
                   'product_type': RAW_MATERIAL_TYPE}
        self.assertEqual(self.client.post(product_list, payload, format='json').status_code, 400)
