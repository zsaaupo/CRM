from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from audit_log.models import AuditLog
from inventory.models import Currency, Product, UnitOfMeasure
from people.models import Party
from sale.models import Chalan, ChalanItem, Courier, Delivery, DeliveryStatus, DeliveryStatusUpdate, PackingSlip
from warehouse.models import Stock, WareHouse
from warehouse.tests.helpers import assign_warehouse_role


class ChalanTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.warehouse = WareHouse.objects.create(name='Main warehouse')
        cls.other_warehouse = WareHouse.objects.create(name='Other warehouse')
        cls.customer = Party.objects.create(code='CH-CUSTOMER', name='Chalan customer', is_customer=True,
                                            address='12 Example Road', phone='01234567890')
        cls.unit = UnitOfMeasure.objects.create(name='Piece', code='CH-PCS')
        cls.product = Product.objects.create(sku='CH-ITEM', name='Chalan product', currency_id='BDT', unit=cls.unit)
        cls.user = get_user_model().objects.create_user(username='chalan-operator')
        cls.role = assign_warehouse_role(cls.user, warehouse=cls.warehouse).role

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)

    def grant(self, *codenames):
        self.role.permissions.add(*Permission.objects.filter(content_type__app_label='sale', codename__in=codenames))

    def item(self, **changes):
        return {'line_number': 1, 'product': self.product.pk, 'unit': self.unit.pk, 'quantity': '2.500001', **changes}

    def create_chalan(self, **changes):
        return self.client.post(reverse('sale:chalan-list'), {
            'customer': self.customer.pk, 'shipping_address': self.customer.address, 'items': [self.item()], **changes,
        }, format='json')

    def test_create_view_search_and_edit_standalone_chalan(self):
        self.grant('add_chalan', 'add_chalanitem', 'view_chalan', 'view_chalanitem', 'change_chalan', 'change_chalanitem')
        stock = Stock.objects.create(warehouse=self.warehouse, product=self.product, quantity=10)
        response = self.create_chalan(customer_reference='DISPATCH-12', chalan_number='attempted override')
        self.assertEqual(response.status_code, 201, response.data)
        chalan = response.data['chalan']
        self.assertTrue(chalan['chalan_number'].startswith('CH-'))
        self.assertEqual(chalan['warehouse'], self.warehouse.pk)
        self.assertEqual(chalan['create_by'], self.user.username)

        listed = self.client.get(reverse('sale:chalan-list-view'), {'q': 'DISPATCH-12'})
        self.assertEqual([row['id'] for row in listed.data['results']], [chalan['id']])
        detail_url = reverse('sale:chalan-detail', args=[chalan['id']])
        original = self.client.get(detail_url).data['items'][0]
        self.assertEqual(original['quantity'], '2.500001')
        report = self.client.patch(reverse('sale:chalan-update', args=[chalan['id']]), {
            'notes': 'Handle carefully',
            'items': [self.item(id=original['id'], quantity='3.000001'), self.item(line_number=2)],
        }, format='json')
        self.assertEqual(report.status_code, 200, report.data)
        updated = self.client.get(detail_url).data
        self.assertEqual(updated['notes'], 'Handle carefully')
        self.assertEqual(len(updated['items']), 2)
        self.assertEqual(updated['items'][0]['id'], original['id'])
        self.assertEqual(updated['items'][0]['created_at'], original['created_at'])
        self.assertEqual(updated['items'][0]['quantity'], '3.000001')
        self.assertTrue(AuditLog.objects.filter(resource='sale.chalanitem', warehouse_id=self.warehouse.pk).exists())
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, Decimal('10'))

    def test_item_permissions_validation_and_atomic_edits(self):
        self.grant('add_chalan', 'view_chalan', 'change_chalan')
        self.assertEqual(self.create_chalan().status_code, 403)
        self.grant('add_chalanitem')
        other_unit = UnitOfMeasure.objects.create(name='Box', code='CH-BOX')
        for payload in ({'items': []}, {'items': [self.item(unit=other_unit.pk)]},
                        {'items': [self.item(), self.item()]},
                        {'chalan_date': '2026-01-10', 'expected_delivery_date': '2026-01-09'}):
            with self.subTest(payload=payload):
                self.assertEqual(self.create_chalan(**payload).status_code, 400)
        self.assertFalse(Chalan.objects.exists())
        response = self.create_chalan(items=[self.item(), self.item(line_number=2)])
        self.assertEqual(response.status_code, 201, response.data)
        chalan = Chalan.objects.get(pk=response.data['chalan']['id'])
        line = chalan.items.first()
        detail = reverse('sale:chalan-detail', args=[chalan.pk])
        self.assertNotIn('items', self.client.get(detail).data)
        self.assertEqual(self.client.patch(detail, {'notes': 'Header edit'}, format='json').status_code, 200)
        response = self.client.patch(detail, {'items': [{'id': line.pk, 'quantity': '4'}]}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        response = self.client.patch(detail, {'notes': 'Must roll back', 'items': [self.item(id=line.pk)]}, format='json')
        self.assertEqual(response.status_code, 403, response.data)
        chalan.refresh_from_db()
        self.assertEqual(chalan.notes, 'Header edit')
        self.assertEqual(chalan.items.count(), 2)

    def test_prices_use_sale_rounding_and_are_preserved_by_quantity_only_edits(self):
        self.grant('add_chalan', 'add_chalanitem', 'view_chalan', 'view_chalanitem', 'change_chalan', 'change_chalanitem')
        first = self.item(quantity='3', unit_price='10', discount_amount='2', tax_rate='15')
        rounding = self.item(line_number=2, quantity='0.0005', unit_price='0.1', tax_rate='50')
        response = self.create_chalan(currency='BDT', shipping_cost='5', items=[first, rounding], grand_total='0')
        self.assertEqual(response.status_code, 201, response.data)
        chalan = response.data['chalan']
        self.assertEqual((chalan['subtotal'], chalan['tax_total'], chalan['grand_total']),
                         ('28.0001', '4.2001', '37.2002'))
        detail_url = reverse('sale:chalan-detail', args=[chalan['id']])
        detail = self.client.get(detail_url).data
        self.assertEqual((detail['items'][1]['net_amount'], detail['items'][1]['tax_amount']), ('0.0001', '0.0001'))
        line_ids = [line['id'] for line in detail['items']]
        response = self.client.patch(detail_url, {'items': [
            self.item(id=line_ids[0], quantity='4'), self.item(id=line_ids[1], line_number=2, quantity='0.0005'),
        ]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['grand_total'], '48.7002')
        self.assertEqual(self.client.get(detail_url).data['items'][0]['discount_amount'], '2.0000')

        response = self.client.patch(detail_url, {'shipping_cost': '7', 'items': [
            self.item(id=line_ids[0], quantity='0.1'), self.item(id=line_ids[1], line_number=2, quantity='0.0005'),
        ]}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('discount_amount', response.data['items'][0])
        self.assertEqual(self.client.get(detail_url).data['shipping_cost'], '5.0000')
        self.assertEqual(self.client.get(reverse('sale:chalan-list-view')).data['results'][0]['grand_total'], '48.7002')

    def test_currency_and_catalog_pricing_options_match_sales(self):
        self.grant('add_chalan', 'add_chalanitem', 'add_salesorder', 'add_salesorderitem')
        Currency.objects.create(code='USD', name='Dollar', exchange_rate='115.5')
        self.product.sale_price, self.product.product_tax = Decimal('12.5'), Decimal('7.5')
        self.product.save(update_fields=['sale_price', 'product_tax'])
        chalan_options = self.client.get(reverse('sale:chalan-options')).data
        sales_options = self.client.get(reverse('sale:sale-options')).data
        self.assertEqual(chalan_options['currency'], sales_options['currency'])
        self.assertEqual(chalan_options['base_currency'], sales_options['base_currency'])
        for key in ('currency', 'sale_price', 'tax_rate'):
            self.assertEqual(chalan_options['product'][0][key], sales_options['product'][0][key])
        self.assertEqual(self.create_chalan(currency='USD').status_code, 400)
        self.assertEqual(self.create_chalan(currency='BDT', exchange_rate='2').status_code, 400)
        response = self.create_chalan(currency='USD', exchange_rate='115.5', shipping_cost='2',
                                      items=[self.item(quantity='2', unit_price='3', tax_rate='10')])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['chalan']['grand_total'], '8.6000')

    def test_warehouse_rules_and_sales_navigation(self):
        outside = Chalan.objects.create(warehouse=self.other_warehouse, customer=self.customer, shipping_address='Other')
        list_page = reverse('sale_pages:chalan-list')
        self.assertEqual(self.client.get(list_page).status_code, 403)
        self.grant('view_chalan', 'add_chalan', 'add_chalanitem')
        page = self.client.get(list_page)
        self.assertContains(page, f'href="{list_page}"')
        self.assertContains(page, 'Create Chalan')
        self.assertContains(self.client.get(reverse('sale_pages:chalan-create')), 'data-chalan-form')
        options = self.client.get(reverse('sale:chalan-options')).data
        self.assertEqual(options['warehouse_scope'], {'all_warehouses': False, 'warehouse': self.warehouse.pk})
        self.assertEqual([row['value'] for row in options['warehouse']], [self.warehouse.pk])
        self.assertEqual(self.create_chalan(warehouse=self.other_warehouse.pk).status_code, 403)
        self.assertEqual(self.client.get(reverse('sale:chalan-list-view')).data['count'], 0)
        self.assertEqual(self.client.get(reverse('sale:chalan-detail', args=[outside.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('sale_pages:chalan-detail', args=[outside.pk])).status_code, 404)

        created = self.create_chalan().data['chalan']['id']
        detail = reverse('sale_pages:chalan-detail', args=[created])
        edit = reverse('sale_pages:chalan-edit', args=[created])
        self.assertNotContains(self.client.get(detail), 'Edit Chalan')
        self.assertEqual(self.client.get(edit).status_code, 403)
        self.grant('view_chalanitem', 'change_chalan', 'change_chalanitem', 'delete_chalanitem')
        self.assertContains(self.client.get(detail), 'Edit Chalan')
        self.assertContains(self.client.get(edit), reverse('sale:chalan-update', args=[created]))
        self.assertEqual(self.client.get(reverse('sale_pages:chalan-edit', args=[outside.pk])).status_code, 404)

    def test_existing_customer_and_warehouse_links_survive_deactivation(self):
        self.grant('add_chalan', 'add_chalanitem', 'view_chalan', 'change_chalan')
        pk = self.create_chalan().data['chalan']['id']
        Party.objects.filter(pk=self.customer.pk).delete()
        WareHouse.objects.filter(pk=self.warehouse.pk).update(is_active=False)
        self.assertEqual(self.create_chalan().status_code, 400)
        response = self.client.patch(reverse('sale:chalan-update', args=[pk]), {
            'customer': self.customer.pk, 'warehouse': self.warehouse.pk, 'notes': 'Historical record',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['customer_name'], self.customer.name)
        self.assertEqual(ChalanItem.objects.filter(chalan_id=pk).count(), 1)

    def test_generate_once_then_search_and_download_from_chalan_or_list(self):
        self.grant('add_chalan', 'add_chalanitem', 'view_chalan', 'view_chalanitem', 'view_packingslip')
        chalan = self.create_chalan().data['chalan']
        detail = reverse('sale_pages:chalan-detail', args=[chalan['id']])
        generate = reverse('sale:packing-slip-generate', args=[chalan['id']])
        list_api = reverse('sale:packing-slip-list')
        list_page = reverse('sale_pages:packing-slip-list')
        self.assertEqual(self.client.get(list_api).data['count'], 0)
        self.assertNotContains(self.client.get(detail), 'Generate packing slip')
        self.assertEqual(self.client.post(generate, {}, format='json').status_code, 403)

        self.grant('add_packingslip')
        self.assertContains(self.client.get(detail), f'action="{generate}"')
        created = self.client.post(generate, {}, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        slip = created.data['packing_slip']
        self.assertTrue(slip['slip_number'].startswith('PS-'))
        self.assertEqual(slip['chalan'], chalan['id'])
        repeated = self.client.post(generate, {}, format='json')
        self.assertEqual(repeated.status_code, 200)
        self.assertFalse(repeated.data['created'])
        self.assertEqual(repeated.data['packing_slip'], slip)
        self.assertEqual(PackingSlip.objects.count(), 1)

        for query in (slip['slip_number'], chalan['chalan_number'], self.customer.name):
            self.assertEqual(self.client.get(list_api, {'q': query}).data['results'], [slip])
        self.assertEqual(self.client.get(list_api, {'q': 'no matching slips'}).data['count'], 0)
        pdf_url = reverse('report:packing-slip-pdf', args=[slip['id']])
        self.assertContains(self.client.get(detail), f'data-report-url="{pdf_url}"')
        page = self.client.get(list_page)
        self.assertContains(page, f'href="{list_page}"')
        pdf_template = reverse('report:packing-slip-pdf', args=[0])
        self.assertContains(page, f'data-report-url-template="{pdf_template}"')
        self.assertTrue(AuditLog.objects.filter(resource='sale.packingslip', action='created',
            warehouse_id=self.warehouse.pk, reference=slip['slip_number']).exists())

    def test_packing_slip_requires_view_permissions_and_a_chalan_in_scope_with_items(self):
        empty = Chalan.objects.create(warehouse=self.warehouse, customer=self.customer, shipping_address='In scope')
        outside = Chalan.objects.create(warehouse=self.other_warehouse, customer=self.customer, shipping_address='Other')
        PackingSlip.objects.create(chalan=outside)
        list_api = reverse('sale:packing-slip-list')
        list_page = reverse('sale_pages:packing-slip-list')
        self.grant('view_chalan')
        self.assertEqual(self.client.get(list_api).status_code, 403)
        self.assertEqual(self.client.get(list_page).status_code, 403)
        self.grant('view_packingslip', 'add_packingslip')
        self.assertEqual(self.client.get(list_api).data['count'], 0)
        self.assertContains(self.client.get(list_page), 'data-report-url-template=""')
        generate = reverse('sale:packing-slip-generate', args=[empty.pk])
        self.assertEqual(self.client.post(generate, {}, format='json').status_code, 403)
        self.grant('view_chalanitem')
        self.assertEqual(self.client.post(generate, {}, format='json').status_code, 400)
        self.assertEqual(self.client.post(reverse('sale:packing-slip-generate', args=[outside.pk]), {}, format='json').status_code, 404)
        self.assertFalse(PackingSlip.objects.filter(chalan=empty).exists())

    def test_delete_chalan_checks_permissions_and_removes_linked_records_atomically(self):
        self.grant('add_chalan', 'add_chalanitem', 'view_chalan')
        created = self.create_chalan().data['chalan']
        chalan = Chalan.objects.get(pk=created['id'])
        self.assertFalse(created['is_delivered'])
        slip = PackingSlip.objects.create(chalan=chalan)
        courier = Courier.objects.create(name='Delete test courier')
        delivery = Delivery.objects.create(chalan=chalan, courier=courier, status=DeliveryStatus.IN_TRANSIT)
        event = DeliveryStatusUpdate.objects.create(delivery=delivery, status=delivery.status, create_by=self.user.username)
        outside = Chalan.objects.create(warehouse=self.other_warehouse, customer=self.customer, shipping_address='Other')
        list_page = reverse('sale_pages:chalan-list')
        delete_api = reverse('sale:chalan-detail', args=[chalan.pk])
        self.assertEqual(self.client.delete(delete_api).status_code, 403)
        self.grant('delete_chalan')
        self.assertEqual(self.client.delete(delete_api).status_code, 403)
        self.assertNotContains(self.client.get(list_page), 'Delete Chalan')
        self.grant('delete_chalanitem')
        page = self.client.get(list_page)
        self.assertContains(page, 'Delete Chalan')
        self.assertContains(page, 'data-delete-disabled-field="is_delete_locked"')
        self.assertContains(page, 'Its items, packing slip, and delivery history will also be removed.')
        self.assertEqual(self.client.delete(reverse('sale:chalan-detail', args=[outside.pk])).status_code, 404)

        with patch.object(Chalan, 'delete', side_effect=ProtectedError('Referenced', [])):
            self.assertEqual(self.client.delete(delete_api).status_code, 409)
        self.assertTrue(ChalanItem.objects.filter(chalan=chalan).exists())
        self.assertTrue(PackingSlip.objects.filter(pk=slip.pk).exists())
        self.assertTrue(Delivery.objects.filter(pk=delivery.pk).exists())
        self.assertTrue(DeliveryStatusUpdate.objects.filter(pk=event.pk).exists())

        self.assertEqual(self.client.delete(delete_api).status_code, 204)
        self.assertFalse(Chalan.objects.filter(pk=chalan.pk).exists())
        self.assertFalse(ChalanItem.objects.filter(chalan=chalan).exists())
        self.assertFalse(PackingSlip.objects.filter(pk=slip.pk).exists())
        self.assertFalse(Delivery.objects.filter(pk=delivery.pk).exists())
        self.assertFalse(DeliveryStatusUpdate.objects.filter(pk=event.pk).exists())
        self.assertTrue(Courier.objects.filter(pk=courier.pk).exists())
        self.assertTrue(AuditLog.objects.filter(resource='sale.chalan', action='deleted',
            reference=chalan.chalan_number, warehouse_id=self.warehouse.pk).exists())

    def test_delivered_chalan_is_read_only_on_both_write_routes(self):
        self.grant('add_chalan', 'add_chalanitem', 'view_chalan', 'view_chalanitem', 'change_chalan',
                   'change_chalanitem', 'delete_chalanitem', 'delete_chalan', 'view_delivery', 'change_delivery')
        created = self.create_chalan(notes='Original notes').data['chalan']
        chalan = Chalan.objects.get(pk=created['id'])
        slip = PackingSlip.objects.create(chalan=chalan)
        courier = Courier.objects.create(name='Delivered test courier')
        delivery = Delivery.objects.create(chalan=chalan, courier=courier, status=DeliveryStatus.HANDED_OVER)
        detail_page = reverse('sale_pages:chalan-detail', args=[chalan.pk])
        detail_api = reverse('sale:chalan-detail', args=[chalan.pk])
        update_api = reverse('sale:chalan-update', args=[chalan.pk])
        self.assertFalse(self.client.get(detail_api).data['is_delivered'])
        self.assertContains(self.client.get(detail_page), 'Edit Chalan')

        response = self.client.post(reverse('sale:delivery-status', args=[delivery.pk]),
                                    {'status': DeliveryStatus.DELIVERED}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        listed = self.client.get(reverse('sale:chalan-list-view')).data['results'][0]
        self.assertTrue(listed['is_delivered'])
        page = self.client.get(reverse('sale_pages:chalan-list'))
        self.assertContains(page, 'data-edit-disabled-field="is_delivered"')
        self.assertContains(page, 'data-delete-disabled-field="is_delete_locked"')
        page = self.client.get(detail_page)
        self.assertNotContains(page, 'Edit Chalan')
        self.assertContains(page, 'Delivered Chalans are read-only.')

        line = chalan.items.get()
        payload = {'customer': self.customer.pk, 'shipping_address': chalan.shipping_address,
                   'notes': 'Changed', 'is_delivered': False, 'items': [self.item(id=line.pk, quantity='1')]}
        for url in (detail_api, update_api):
            for method in (self.client.patch, self.client.put):
                result = method(url, payload, format='json')
                self.assertEqual(result.status_code, 409, result.data)
        response = self.client.delete(detail_api)
        self.assertEqual(response.status_code, 409, response.data)
        current = self.client.get(detail_api).data
        self.assertTrue(current['is_delivered'])
        self.assertEqual(current['notes'], 'Original notes')
        self.assertEqual(current['items'][0]['quantity'], '2.500001')
        self.assertTrue(PackingSlip.objects.filter(pk=slip.pk).exists())
        self.assertTrue(Delivery.objects.filter(pk=delivery.pk, status=DeliveryStatus.DELIVERED).exists())
