from warehouse.tests.helpers import assign_warehouse_role
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounting.models import FinancialAccount, PurchasePayment
from common.models import TradeStatus
from inventory.models import Currency, Product, UnitOfMeasure
from people.models import Party
from purchase.models import PurchaseOrder, PurchaseOrderItem
from purchase.serializers import PurchaseOrderItemSerializer, PurchaseOrderSerializer
from warehouse.models import RoleWarehouseAccess, Stock, WareHouse


class PurchaseAPITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='purchase-operator')
        self.role = assign_warehouse_role(self.user, all_warehouses=True).role
        self.payment_account = FinancialAccount.objects.create(name='Purchase bank', type='bank')
        self.unit = UnitOfMeasure.objects.create(name='Pieces', code='PCS')
        self.product = Product.objects.create(sku='KEYBOARD', name='Keyboard', currency_id='BDT', unit=self.unit)
        self.supplier = Party.objects.create(code='SUP-1', name='Example supplier', is_supplier=True)
        self.order = PurchaseOrder.objects.create(currency_id='BDT', exchange_rate=Decimal('1'), supplier=None,
                                                  shipping_cost=Decimal('5'), create_by='original', modified_by='original')
        self.item = PurchaseOrderItem.objects.create(purchase_order=self.order, line_number=1, product=self.product,
                                                     unit=self.unit, quantity=Decimal('2'), unit_price=Decimal('10'),
                                                     create_by='original', modified_by='original')
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=self.token)

    def grant(self, model, *actions):
        self.role.permissions.add(*Permission.objects.filter(content_type__app_label='purchase',
            codename__in=[f'{action}_{model._meta.model_name}' for action in actions]))

    def order_payload(self, **changes):
        return {'currency': 'BDT', 'exchange_rate': '1.00000000', 'supplier': self.supplier.pk,
                'payment_account': self.payment_account.pk, **changes}

    def import_csv(self, contents, **changes):
        payload = self.order_payload(file=SimpleUploadedFile('purchase.csv', contents, content_type='text/csv'), **changes)
        return self.client.post(reverse('purchase:purchase-order-import'), payload, format='multipart')

    def test_csv_import_uses_catalog_cost_and_leaves_stock_and_payment_to_receiving(self):
        self.grant(PurchaseOrder, 'add', 'change')
        self.grant(PurchaseOrderItem, 'add')
        warehouse = WareHouse.objects.create(name='Receiving warehouse')
        stock = Stock.objects.create(warehouse=warehouse, product=self.product, quantity=10)
        self.payment_account.opening_balance = Decimal(100)
        self.payment_account.save(update_fields=['opening_balance'])
        self.product.cost_price, self.product.sale_price, self.product.product_tax = Decimal('12.5'), Decimal(20), Decimal(18)
        self.product.save(update_fields=['cost_price', 'sale_price', 'product_tax'])
        box = UnitOfMeasure.objects.create(name='Box', code='PURCHASE-BOX')
        other = Product.objects.create(sku='001,BOX', name='Box', currency_id='BDT', unit=box)
        response = self.import_csv(
            b'\xef\xbb\xbf Quantity, SKU, unit_price, discount_amount, tax_rate, description\r\n'
            b'2.5,KEYBOARD,,,,\r\n\r\n1,"001,BOX",10,3,10,"Box, small"\r\n',
            warehouse=warehouse.pk, expected_date='', shipping_cost='2', is_received='true',
        )
        self.assertEqual(response.status_code, 201, response.data)
        order = response.data['purchase_order']
        self.assertEqual(order['supplier'], self.supplier.pk)
        self.assertEqual(order['status'], 'draft')
        self.assertFalse(order['is_received'])
        self.assertEqual(order['grand_total'], '40.9500')
        items = list(PurchaseOrderItem.objects.filter(purchase_order_id=order['id']))
        self.assertEqual([item.product_id for item in items], [self.product.pk, other.pk])
        self.assertEqual([item.line_number for item in items], [1, 2])
        self.assertEqual([item.unit_id for item in items], [self.unit.pk, box.pk])
        self.assertEqual([item.tax_rate for item in items], [Decimal(0), Decimal(10)])
        self.assertEqual(items[1].description, 'Box, small')
        self.assertTrue(all(item.create_by == self.user.username for item in items))
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, 10)
        self.assertFalse(Stock.objects.filter(warehouse=warehouse, product=other).exists())
        self.assertFalse(PurchasePayment.objects.filter(purchase_order_id=order['id']).exists())

        received = self.client.post(reverse('purchase:purchase-order-receive'), {
            'purchase_id': order['id'], 'is_received': True,
        }, format='json')
        self.assertEqual(received.status_code, 200, received.data)
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, Decimal('12.5'))
        self.assertEqual(Stock.objects.get(warehouse=warehouse, product=other).quantity, 1)
        payment = PurchasePayment.objects.get(purchase_order_id=order['id'])
        self.assertEqual(payment.amount, Decimal('40.9500'))

    def test_csv_import_rejects_bad_files_and_saves_nothing_on_bad_rows_or_failed_insert(self):
        self.grant(PurchaseOrder, 'add')
        self.grant(PurchaseOrderItem, 'add')
        missing = self.client.post(reverse('purchase:purchase-order-import'), self.order_payload(), format='multipart')
        self.assertEqual(missing.status_code, 400, missing.data)
        self.assertIn('file', missing.data)
        for contents in (b'sku,invalid\nKEYBOARD,2\n', b'sku,quantity\n', b'sku,quantity\n"KEYBOARD,2\n'):
            response = self.import_csv(contents)
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn('file', response.data)
        response = self.import_csv(b'sku,quantity,unit_price,discount_amount,tax_rate\n'
                                   b'KEYBOARD,1,5,0,10\nUNKNOWN,1,5,0,0\nKEYBOARD,0,5,0,0\n'
                                   b'KEYBOARD,1,5,6,0\nKEYBOARD,1,5,0,101\n')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual([message.split(':')[0] for message in response.data['file']], ['Row 3', 'Row 4', 'Row 5', 'Row 6'])
        create = PurchaseOrderItem.objects.create

        def fail_second_line(**values):
            if values['line_number'] == 2:
                raise IntegrityError('line insert failed')
            return create(**values)

        with patch('purchase.serializers.PurchaseOrderItem.objects.create', side_effect=fail_second_line):
            response = self.import_csv(b'sku,quantity\nKEYBOARD,1\nKEYBOARD,2\n')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(PurchaseOrderItem.objects.count(), 1)

    def test_csv_import_requires_an_explicit_price_in_a_different_currency(self):
        self.grant(PurchaseOrder, 'add')
        self.grant(PurchaseOrderItem, 'add')
        Currency.objects.create(code='USD', name='Dollar', exchange_rate=120)
        order = {'currency': 'USD', 'exchange_rate': '120'}
        response = self.import_csv(b'sku,quantity\nKEYBOARD,2\n', **order)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('unit price in USD', response.data['file'][0])
        response = self.import_csv(b'sku,quantity,unit_price\nKEYBOARD,2,3.25\n', **order)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['purchase_order']['grand_total'], '6.5000')

    def test_csv_import_page_template_options_and_warehouse_scope(self):
        self.grant(PurchaseOrder, 'view', 'add')
        url = reverse('purchase:purchase-order-import')
        page = reverse('purchase_pages:import-purchase')
        listing = reverse('purchase_pages:purchase-list')
        sheet = b'sku,quantity\nKEYBOARD,1\n'
        self.assertNotContains(self.client.get(listing), f'href="{page}"')
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get(page).status_code, 403)
        self.assertEqual(self.import_csv(sheet).status_code, 403)

        self.grant(PurchaseOrderItem, 'add')
        self.assertContains(self.client.get(listing), f'href="{page}"')
        self.assertContains(self.client.get(reverse('purchase_pages:create-purchase')), f'href="{page}"')
        import_page = self.client.get(page)
        self.assertContains(import_page, f'action="{url}"')
        self.assertContains(import_page, 'name="file"')
        template = self.client.get(url)
        self.assertEqual(template.status_code, 200)
        self.assertEqual(template.content.decode('utf-8-sig').strip(),
                         'sku,quantity,unit_price,discount_amount,tax_rate,description')

        main = WareHouse.objects.create(name='Assigned branch')
        other = WareHouse.objects.create(name='Other branch')
        RoleWarehouseAccess.objects.filter(role=self.role).update(all_warehouses=False, warehouse=main)
        self.payment_account.warehouse = main
        self.payment_account.save(update_fields=['warehouse'])
        options = self.client.get(reverse('purchase:purchase-import-options'))
        self.assertEqual(options.status_code, 200, options.data)
        self.assertEqual(options.data['warehouse'], [{'value': main.pk, 'label': str(main)}])
        self.assertNotIn('product', options.data)
        denied = self.import_csv(sheet, warehouse=other.pk)
        self.assertEqual(denied.status_code, 403, denied.data)
        response = self.import_csv(sheet)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['purchase_order']['warehouse'], main.pk)

    def test_purchase_creation_requires_an_eligible_payment_account(self):
        self.grant(PurchaseOrder, 'add')
        url = reverse('purchase:purchase-order-list')
        payload = self.order_payload()
        del payload['payment_account']
        branch = WareHouse.objects.create(name='Branch', address='Dhaka', phone='1')
        branch_bank = FinancialAccount.objects.create(name='Branch bank', type='bank', warehouse=branch)
        inactive_bank = FinancialAccount.objects.create(name='Inactive bank', type='bank', is_active=False)
        for changes in ({}, {'payment_account': branch_bank.pk}, {'payment_account': inactive_bank.pk}):
            response = self.client.post(url, {**payload, **changes}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn('payment_account', response.data)

    def item_payload(self, **changes):
        return {'purchase_order': self.order.pk, 'line_number': 2, 'product': self.product.pk, 'unit': self.unit.pk,
                'quantity': '3.000000', 'unit_price': '10.0000', 'discount_amount': '2.0000', 'tax_rate': '15', **changes}

    def resources(self):
        return [(PurchaseOrder, 'purchase-order', 'purchase_order', self.order_payload()),
                (PurchaseOrderItem, 'purchase-order-item', 'purchase_order_item', self.item_payload())]

    def test_both_models_support_crud_with_separate_permissions_and_read_only_fields(self):
        for model, route, key, payload in self.resources():
            with self.subTest(route=route):
                url = reverse(f'purchase:{route}-list')
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url, payload, format='json').status_code, 403)
                self.grant(model, 'add')
                response = self.client.post(url, {**payload, 'id': 9999, 'create_by': 'spoof', 'modified_by': 'spoof',
                                                  'order_number': 'SPOOF', 'subtotal': '999', 'line_total': '999'}, format='json')
                self.assertEqual(response.status_code, 201, response.data)
                self.assertIn('message', response.data)
                data = response.data[key]
                self.assertNotEqual(data['id'], 9999)
                self.assertEqual(data['create_by'], self.user.username)
                self.assertEqual(data['modified_by'], self.user.username)
                if model is PurchaseOrder:
                    self.assertNotEqual(data['order_number'], 'SPOOF')
                    self.assertEqual(data['subtotal'], '0.0000')
                    self.assertEqual(data['status'], 'draft')
                else:
                    self.assertEqual(data['net_amount'], '28.0000')
                    self.assertEqual(data['tax_amount'], '4.2000')
                    self.assertEqual(data['line_total'], '32.2000')
                detail = reverse(f'purchase:{route}-detail', args=[data['id']])
                update = reverse(f'purchase:{route}-update', args=[data['id']])
                self.assertEqual(self.client.get(detail).status_code, 403)
                self.assertEqual(self.client.patch(update, {'notes': 'Denied'}, format='json').status_code, 403)
                self.grant(model, 'view', 'change')
                self.assertEqual(self.client.get(detail).status_code, 200)
                field = 'notes' if model is PurchaseOrder else 'description'
                updated = self.client.patch(update, {field: 'Updated', 'create_by': 'spoof'}, format='json')
                self.assertEqual(updated.status_code, 200, updated.data)
                self.assertEqual(updated.data[field], 'Updated')
                self.assertEqual(updated.data['create_by'], self.user.username)
                self.assertEqual(self.client.put(detail, payload, format='json').status_code, 200)
                self.assertEqual(self.client.delete(detail).status_code, 403)
                self.grant(model, 'delete')
                self.assertEqual(self.client.delete(detail).status_code, 204)
                self.assertFalse(model.objects.filter(pk=data['id']).exists())
                self.assertEqual(self.client.get(detail).status_code, 404)

    def test_order_optional_supplier_currency_and_shared_status_codes(self):
        self.grant(PurchaseOrder, 'add', 'change')
        url = reverse('purchase:purchase-order-list')
        for supplier in [None, self.supplier.pk]:
            response = self.client.post(url, self.order_payload(supplier=supplier), format='json')
            self.assertEqual(response.status_code, 201, response.data)
            self.assertEqual(response.data['purchase_order']['supplier'], supplier)
        status = TradeStatus.objects.create(code='review-required', name='Review required')
        response = self.client.patch(reverse('purchase:purchase-order-update', args=[self.order.pk]),
                                     {'status': status.code}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'review-required')
        Currency.objects.create(code='USD', name='Dollar', exchange_rate='120')
        response = self.client.post(url, self.order_payload(currency='USD', exchange_rate='120'), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['purchase_order']['currency'], 'USD')

    def test_order_validation_checks_merged_partial_updates_and_required_fields(self):
        self.grant(PurchaseOrder, 'add', 'change')
        url = reverse('purchase:purchase-order-list')
        detail = reverse('purchase:purchase-order-update', args=[self.order.pk])
        cases = [{'exchange_rate': '0'}, {'exchange_rate': '-1'}, {'exchange_rate': '2'},
                 {'shipping_cost': '-1'}, {'status': 'not-real'}, {'currency': 'ZZZ'},
                 {'expected_date': str(self.order.order_date - timedelta(days=1))}]
        for invalid in cases:
            with self.subTest(payload=invalid):
                self.assertEqual(self.client.post(url, self.order_payload(**invalid), format='json').status_code, 400)
                self.assertEqual(self.client.patch(detail, invalid, format='json').status_code, 400)
        self.assertEqual(self.client.post(url, {'currency': 'BDT'}, format='json').status_code, 400)
        self.assertEqual(self.client.post(url, {'exchange_rate': '1'}, format='json').status_code, 400)

    def test_item_validation_unit_matching_references_and_duplicate_lines(self):
        self.grant(PurchaseOrderItem, 'add', 'change')
        other_unit = UnitOfMeasure.objects.create(name='Boxes', code='BOX')
        url = reverse('purchase:purchase-order-item-list')
        update = reverse('purchase:purchase-order-item-update', args=[self.item.pk])
        cases = [{'quantity': '0'}, {'quantity': '-1'}, {'quantity': '1.1234567'}, {'unit_price': '-1'},
                 {'discount_amount': '-1'}, {'discount_amount': '999'}, {'tax_rate': '101'},
                 {'tax_rate': '-1'}, {'line_number': 0}, {'unit': other_unit.pk}, {'product': 9999},
                 {'purchase_order': 9999}, {'line_number': 1}]
        for invalid in cases:
            self.assertEqual(self.client.post(url, self.item_payload(**invalid), format='json').status_code, 400)
        response = self.client.patch(update, {'quantity': '0.01', 'discount_amount': '1'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, Decimal('2'))
        self.assertEqual(PurchaseOrderItem.objects.count(), 1)

    def test_totals_follow_item_create_update_delete_and_ignore_client_totals(self):
        self.grant(PurchaseOrder, 'view', 'change')
        self.grant(PurchaseOrderItem, 'add', 'change', 'delete')
        detail = reverse('purchase:purchase-order-detail', args=[self.order.pk])
        self.assertEqual(self.client.get(detail).data['grand_total'], '25.0000')
        response = self.client.post(reverse('purchase:purchase-order-item-list'), self.item_payload(), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        item_id = response.data['purchase_order_item']['id']
        self.assertEqual(self.client.get(detail).data['grand_total'], '57.2000')
        response = self.client.patch(reverse('purchase:purchase-order-item-update', args=[item_id]),
                                     {'quantity': '4', 'line_total': '999'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['line_total'], '43.7000')
        response = self.client.patch(detail, {'grand_total': '999', 'subtotal': '999'}, format='json')
        self.assertEqual(response.data['grand_total'], '68.7000')
        self.assertEqual(self.client.delete(reverse('purchase:purchase-order-item-detail', args=[item_id])).status_code, 204)
        self.assertEqual(self.client.get(detail).data['grand_total'], '25.0000')
        self.assertFalse(Stock.objects.exists())

    def test_order_deletion_requires_item_permission_and_missing_records_return_404(self):
        self.grant(PurchaseOrder, 'delete', 'view', 'change')
        response = self.client.delete(reverse('purchase:purchase-order-detail', args=[self.order.pk]))
        self.assertEqual(response.status_code, 403, response.data)
        self.assertTrue(PurchaseOrder.objects.filter(pk=self.order.pk).exists())
        for model, route, key, payload in self.resources():
            self.grant(model, 'view', 'change', 'delete')
            detail = reverse(f'purchase:{route}-detail', args=[99999])
            self.assertEqual(self.client.get(detail).status_code, 404)
            self.assertEqual(self.client.patch(detail, {}, format='json').status_code, 404)
            self.assertEqual(self.client.delete(detail).status_code, 404)

    def test_delete_order_removes_its_items_atomically(self):
        self.grant(PurchaseOrder, 'delete')
        self.grant(PurchaseOrderItem, 'delete')
        url = reverse('purchase:purchase-order-detail', args=[self.order.pk])
        with patch('purchase.models.PurchaseOrder.delete', side_effect=ProtectedError('Still referenced', [self.order])):
            response = self.client.delete(url)
            self.assertEqual(response.status_code, 409, response.data)
        self.assertTrue(PurchaseOrder.objects.filter(pk=self.order.pk).exists())
        self.assertTrue(PurchaseOrderItem.objects.filter(pk=self.item.pk).exists())
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertFalse(PurchaseOrder.objects.filter(pk=self.order.pk).exists())
        self.assertFalse(PurchaseOrderItem.objects.filter(pk=self.item.pk).exists())

    def test_csrf_is_required_for_every_write(self):
        self.client.credentials()
        for model, route, key, payload in self.resources():
            self.grant(model, 'add', 'change', 'delete')
            pk = self.order.pk if model is PurchaseOrder else self.item.pk
            detail = reverse(f'purchase:{route}-detail', args=[pk])
            for method, url, data in [('post', reverse(f'purchase:{route}-list'), payload),
                                      ('put', detail, payload), ('patch', detail, {}), ('delete', detail, {}),
                                      ('patch', reverse(f'purchase:{route}-update', args=[pk]), {})]:
                self.assertEqual(getattr(self.client, method)(url, data, format='json').status_code, 403)

    def test_anonymous_inactive_and_unrelated_permissions_do_not_grant_access(self):
        self.user.is_staff = True
        self.role.permissions.add(Permission.objects.get(content_type__app_label='inventory', codename='view_product'))
        self.user.save(update_fields=['is_staff'])
        for model, route, key, payload in self.resources():
            url = reverse(f'purchase:{route}-list')
            self.assertEqual(APIClient().get(url).status_code, 403)
            self.assertEqual(self.client.get(url).status_code, 403)
            self.grant(model, 'view')
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.assertEqual(self.client.get(reverse('purchase:purchase-order-list')).status_code, 403)

    def test_group_grants_and_revocation(self):
        role = Group.objects.create(name='Purchase readers')
        role.permissions.add(Permission.objects.get(content_type__app_label='purchase', codename='view_purchaseorder'))
        assign_warehouse_role(self.user, all_warehouses=True, role=role)
        url = reverse('purchase:purchase-order-list')
        self.assertEqual(self.client.get(url).status_code, 200)
        role.permissions.clear()
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_list_search_ordering_filters_and_pagination(self):
        self.grant(PurchaseOrder, 'view')
        self.grant(PurchaseOrderItem, 'view')
        self.supplier.name = 'Search supplier'
        self.supplier.save()
        self.order.supplier = self.supplier
        self.order.save()
        url = reverse('purchase:purchase-order-list')
        self.assertEqual(self.client.get(url, {'q': 'SEARCH SUPPLIER'}).data['count'], 1)
        PurchaseOrder.objects.bulk_create([PurchaseOrder(currency_id='BDT', exchange_rate='1') for _ in range(21)])
        first = self.client.get(url)
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data['count'], 22)
        self.assertEqual(len(first.data['results']), 20)
        self.assertEqual(len(self.client.get(first.data['next']).data['results']), 2)
        self.assertNotEqual(first.data['results'][0]['id'], self.order.pk)
        items = reverse('purchase:purchase-order-item-list-view')
        self.assertEqual(self.client.get(items, {'q': 'KEYBOARD', 'purchase_order': self.order.pk}).data['count'], 1)
        self.assertEqual(self.client.get(items, {'purchase_order': 99999}).data['results'], [])
        for invalid in ['abc', '0', '-1']:
            self.assertEqual(self.client.get(items, {'purchase_order': invalid}).status_code, 400)
        self.assertEqual(self.client.get(url, {'page': 999}).status_code, 404)

    def test_read_only_list_methods_and_change_only_update_access(self):
        for model, route, key, payload in self.resources():
            self.grant(model, 'view')
            url = reverse(f'purchase:{route}-list-view')
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertIn('no-store', response['Cache-Control'])
            self.assertEqual(self.client.head(url).status_code, 200)
            self.assertEqual(self.client.options(url).status_code, 200)
            for method in ['post', 'put', 'patch', 'delete']:
                self.assertEqual(getattr(self.client, method)(url, {}, format='json').status_code, 405)
        self.role.permissions.clear()
        for model, route, key, payload in self.resources():
            self.grant(model, 'change')
            pk = self.order.pk if model is PurchaseOrder else self.item.pk
            url = reverse(f'purchase:{route}-update', args=[pk])
            self.assertEqual(self.client.patch(url, {}, format='json').status_code, 200)
            self.assertEqual(self.client.options(url).status_code, 200)
            self.assertEqual(self.client.get(url).status_code, 405)

    def test_serialization_avoids_queries_per_order_or_item(self):
        PurchaseOrder.objects.bulk_create([PurchaseOrder(currency_id='BDT', exchange_rate='1') for _ in range(5)])
        with self.assertNumQueries(2):
            rows = PurchaseOrderSerializer(PurchaseOrder.objects.select_related('currency', 'status', 'supplier').prefetch_related('items'), many=True).data
        self.assertEqual(len(rows), 6)
        with self.assertNumQueries(1):
            rows = PurchaseOrderItemSerializer(PurchaseOrderItem.objects.select_related('purchase_order', 'product', 'unit'), many=True).data
        self.assertEqual(len(rows), 1)

    def test_multipart_creation_and_transaction_error_handling(self):
        self.grant(PurchaseOrder, 'add', 'change')
        self.grant(PurchaseOrderItem, 'add')
        response = self.client.post(reverse('purchase:purchase-order-list'), self.order_payload(), format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        response = self.client.post(reverse('purchase:purchase-order-item-list'), self.item_payload(), format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        with patch('purchase.serializers.PurchaseOrderSerializer.save', side_effect=IntegrityError):
            response = self.client.patch(reverse('purchase:purchase-order-update', args=[self.order.pk]),
                                         {'supplier': self.supplier.pk}, format='json')
        self.assertEqual(response.status_code, 400)
        self.order.refresh_from_db()
        self.assertIsNone(self.order.supplier)
