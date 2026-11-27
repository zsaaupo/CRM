from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from inventory.models import Product, UnitOfMeasure
from warehouse.models import Stock, StockMovement, WareHouse, WarehouseTransfer, WarehouseTransferItem
from .helpers import assign_warehouse_role


class WarehouseTransferAPITests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(username='transfer-root', email='root@example.com')
        self.source = WareHouse.objects.create(name='Source', address='Dhaka', phone='1')
        self.destination = WareHouse.objects.create(name='Destination', address='Dhaka', phone='2')
        self.other = WareHouse.objects.create(name='Other', address='Dhaka', phone='3')
        unit = UnitOfMeasure.objects.create(name='Pieces', code='API-TRANSFER-PCS')
        self.product = Product.objects.create(name='Transfer item', sku='TRANSFER-API', unit=unit, currency_id='BDT')
        self.source_stock = Stock.objects.create(warehouse=self.source, product=self.product, quantity=10)
        self.record = WarehouseTransfer.objects.create(source_warehouse=self.source, destination_warehouse=self.destination,
            create_by='original', modified_by='original')
        self.item = WarehouseTransferItem.objects.create(transfer=self.record, line_number=1, product=self.product,
            unit=self.product.unit, quantity='2.500000', create_by='original', modified_by='original')
        self.client = APIClient(enforce_csrf_checks=True)
        self.login(self.actor)
        self.list_url = reverse('warehouse:transfer-list')
        self.detail_url = reverse('warehouse:transfer-detail', args=[self.record.pk])
        self.update_url = reverse('warehouse:transfer-update', args=[self.record.pk])
        self.complete_url = reverse('warehouse:transfer-complete')

    def login(self, user):
        self.client.force_login(user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)

    def line(self, **changes):
        return {'line_number': 1, 'product': self.product.pk, 'unit': self.product.unit_id, 'quantity': '3.000000', **changes}

    def payload(self, **changes):
        item = self.line(**{key: changes.pop(key) for key in ['product', 'quantity'] if key in changes})
        return {'source_warehouse': self.source.pk, 'destination_warehouse': self.destination.pk,
                'items': [item], **changes}

    def complete(self, **changes):
        return self.client.post(self.complete_url, {'transfer_id': self.record.pk, 'is_completed': True, **changes}, format='json')

    def balances(self):
        return dict(Stock.objects.filter(product=self.product).values_list('warehouse_id', 'quantity'))

    def stage(self, action):
        return self.client.post(reverse(f'warehouse:transfer-{action}', args=[self.record.pk]), {}, format='json')

    def test_dispatch_and_receipt_are_independent_and_replay_safe(self):
        self.assertEqual(self.stage('receive').status_code, 400)
        sent = self.stage('dispatch')
        self.assertEqual(sent.status_code, 200, sent.data)
        self.assertEqual(sent.data['warehouse_transfer']['stage'], 'in_transit')
        self.assertTrue(sent.data['warehouse_transfer']['locked'])
        self.assertFalse(self.stage('dispatch').data['changed'])
        self.assertEqual(self.balances(), {self.source.pk: Decimal('7.5')})
        self.assertEqual(StockMovement.objects.get().quantity, Decimal('-2.5'))
        for url in (self.list_url, reverse('warehouse:transfer-list-view')):
            self.assertEqual(self.client.get(url, {'stage': 'in_transit'}).data['count'], 1)
            self.assertEqual(self.client.get(url, {'stage': 'pending'}).data['count'], 0)
        self.assertEqual(self.client.patch(self.update_url, {'notes': 'changed'}, format='json').status_code, 409)
        self.assertEqual(self.client.delete(self.detail_url).status_code, 409)
        Stock.objects.filter(pk=self.source_stock.pk).update(quantity=0)
        received = self.stage('receive')
        self.assertEqual(received.data['warehouse_transfer']['stage'], 'received')
        self.assertFalse(self.stage('receive').data['changed'])
        self.assertFalse(self.complete().data['completed_now'])
        self.assertEqual(self.balances(), {self.source.pk: Decimal('0'), self.destination.pk: Decimal('2.5')})
        self.assertEqual(StockMovement.objects.count(), 2)

    def test_failed_receipt_can_be_retried_through_direct_completion(self):
        self.assertTrue(self.stage('dispatch').data['changed'])
        destination = Stock.objects.create(warehouse=self.destination, product=self.product, quantity='999999999999')
        self.assertEqual(self.stage('receive').status_code, 400)
        self.record.refresh_from_db()
        self.assertEqual(self.record.stage, 'in_transit')
        self.assertIsNone(self.record.received_at)
        self.assertEqual(StockMovement.objects.count(), 1)
        Stock.objects.filter(pk=destination.pk).update(quantity=1)
        self.assertTrue(self.complete().data['completed_now'])
        self.assertEqual(self.balances(), {self.source.pk: Decimal('7.5'), self.destination.pk: Decimal('3.5')})
        self.assertFalse(self.stage('dispatch').data['changed'])

    def test_draft_crud_uses_audit_and_never_changes_stock(self):
        response = self.client.post(self.list_url, self.payload(is_completed=True), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        created = response.data['warehouse_transfer']
        self.assertTrue(created['transfer_number'].startswith('WT-'))
        self.assertFalse(created['is_completed'])
        self.assertEqual(created['create_by'], self.actor.username)
        response = self.client.patch(self.update_url, {'items': [self.line(id=self.item.pk, quantity='4.125000')], 'notes': 'Updated', 'is_completed': True}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['items'][0]['quantity'], '4.125000')
        self.assertFalse(response.data['is_completed'])
        self.assertEqual(response.data['create_by'], 'original')
        self.assertEqual(response.data['modified_by'], self.actor.username)
        self.assertEqual(self.balances(), {self.source.pk: Decimal('10')})
        self.assertEqual(self.client.delete(self.detail_url).status_code, 204)
        self.assertEqual(self.balances(), {self.source.pk: Decimal('10')})

    def test_invalid_drafts_are_rejected_without_mutation(self):
        for changes in [{'destination_warehouse': self.source.pk}, {'quantity': '0'}, {'quantity': '-1'},
                        {'quantity': '1000000000000'}, {'quantity': '0.0000001'}, {'product': 999999},
                        {'source_warehouse': None}]:
            response = self.client.post(self.list_url, self.payload(**changes), format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(WarehouseTransfer.objects.count(), 1)
        self.assertEqual(self.balances(), {self.source.pk: Decimal('10')})

    def test_complete_creates_destination_once_and_repeat_does_not_move_again(self):
        response = self.complete()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['completed_now'])
        self.assertTrue(response.data['warehouse_transfer']['is_completed'])
        self.assertEqual(self.balances(), {self.source.pk: Decimal('7.5'), self.destination.pk: Decimal('2.5')})
        self.assertEqual(Stock.objects.filter(product=self.product).aggregate(total=Sum('quantity'))['total'], Decimal('10'))
        response = self.complete()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['completed_now'])
        self.assertEqual(self.balances()[self.source.pk], Decimal('7.5'))
        self.assertEqual(Stock.objects.filter(warehouse=self.destination, product=self.product).count(), 1)

    def test_completion_updates_existing_destination_and_preserves_other_warehouse(self):
        destination = Stock.objects.create(warehouse=self.destination, product=self.product, quantity=4)
        Stock.objects.create(warehouse=self.other, product=self.product, quantity=8)
        response = self.complete()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.balances(), {self.source.pk: Decimal('7.5'), self.destination.pk: Decimal('6.5'), self.other.pk: Decimal('8')})
        destination.refresh_from_db()
        self.assertEqual(destination.modified_by, self.actor.username)

    def test_shortage_or_missing_source_leaves_no_destination_or_completion(self):
        for missing in [False, True]:
            if missing:
                self.source_stock.delete()
            else:
                Stock.objects.filter(pk=self.source_stock.pk).update(quantity=1)
            response = self.complete()
            self.assertEqual(response.status_code, 400, response.data)
            self.record.refresh_from_db(); self.assertFalse(self.record.is_completed)
            self.assertFalse(Stock.objects.filter(warehouse=self.destination).exists())

    def test_inactive_warehouses_cannot_be_selected_or_completed(self):
        self.destination.is_active = False; self.destination.save(update_fields=['is_active'])
        self.assertEqual(self.client.post(self.list_url, self.payload(), format='json').status_code, 400)
        self.assertEqual(self.client.patch(self.update_url, {'notes': 'Keep historical warehouse'}, format='json').status_code, 200)
        self.assertEqual(self.complete().status_code, 400)
        self.record.refresh_from_db(); self.assertFalse(self.record.is_completed)

    def test_overflow_and_late_failure_roll_back_both_balances_and_flag(self):
        destination = Stock.objects.create(warehouse=self.destination, product=self.product, quantity='999999999999.000000')
        before = self.balances()
        self.assertEqual(self.complete().status_code, 400)
        self.assertEqual(self.balances(), before)
        Stock.objects.filter(pk=destination.pk).update(quantity=0)
        original = Stock.objects.filter
        def fail_destination(*args, **kwargs):
            query = original(*args, **kwargs)
            if kwargs.get('pk') == destination.pk:
                query.update = Mock(side_effect=IntegrityError('late destination failure'))
            return query
        with patch('warehouse.services.Stock.objects.filter', side_effect=fail_destination):
            response = self.complete()
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(self.balances(), {self.source.pk: Decimal('10'), self.destination.pk: Decimal('0')})
        self.record.refresh_from_db(); self.assertFalse(self.record.is_completed)

    def test_completed_transfers_reject_all_edits_and_deletion(self):
        self.assertEqual(self.complete().status_code, 200)
        for method, url, data in [('patch', self.update_url, {'notes': 'Wrong'}),
                                  ('put', self.update_url, self.payload()),
                                  ('patch', self.detail_url, {'items': [self.line(id=self.item.pk, quantity='1')]}), ('delete', self.detail_url, {})]:
            response = getattr(self.client, method)(url, data, format='json')
            self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(self.balances()[self.source.pk], Decimal('7.5'))

    def test_invalid_completion_requests_methods_and_csrf(self):
        for data in [{'transfer_id': self.record.pk, 'is_completed': False}, {'transfer_id': self.record.pk},
                     {'transfer_id': 'bad', 'is_completed': True}]:
            self.assertEqual(self.client.post(self.complete_url, data, format='json').status_code, 400)
        self.assertEqual(self.complete(transfer_id=999999).status_code, 404)
        self.assertEqual(self.client.get(self.complete_url).status_code, 405)
        no_csrf = APIClient(enforce_csrf_checks=True); no_csrf.force_login(self.actor)
        self.assertEqual(no_csrf.post(self.complete_url, {'transfer_id': self.record.pk, 'is_completed': True}, format='json').status_code, 403)
        self.assertEqual(APIClient().get(self.list_url).status_code, 403)
        self.assertEqual(APIClient().post(self.complete_url, {}, format='json').status_code, 403)

    def test_specific_warehouse_can_read_incoming_outgoing_but_not_write(self):
        user = get_user_model().objects.create_user(username='transfer-scoped')
        access = assign_warehouse_role(user, warehouse=self.destination)
        access.role.permissions.set(Permission.objects.filter(content_type__app_label='warehouse',
            content_type__model='warehousetransfer'))
        foreign = WarehouseTransfer.objects.create(source_warehouse=self.source, destination_warehouse=self.other)
        WarehouseTransferItem.objects.create(transfer=foreign, line_number=1, product=self.product, unit=self.product.unit, quantity=1)
        self.login(user)
        listing = self.client.get(self.list_url)
        self.assertEqual(listing.status_code, 200, listing.data)
        self.assertEqual(listing.data['count'], 1)
        self.assertEqual(self.client.get(self.detail_url).status_code, 200)
        self.assertEqual(self.client.get(reverse('warehouse:transfer-detail', args=[foreign.pk])).status_code, 404)
        self.assertEqual(self.client.patch(self.update_url, {'quantity': '1'}, format='json').status_code, 404)
        self.assertEqual(self.client.delete(self.detail_url).status_code, 404)
        self.assertEqual(self.client.post(self.list_url, self.payload(), format='json').status_code, 403)
        self.assertEqual(self.complete().status_code, 404)
        for action in ('dispatch', 'receive'):
            self.assertEqual(self.stage(action).status_code, 404)
        access.warehouse = self.source; access.save(update_fields=['warehouse'])
        self.assertEqual(self.client.get(self.list_url).data['count'], 2)
        self.assertEqual(self.client.get(self.list_url, {'q': self.record.transfer_number}).data['count'], 1)
        access.all_warehouses = True; access.warehouse = None; access.save()
        self.assertEqual(self.complete().status_code, 200)
        access.all_warehouses = False; access.warehouse = self.destination; access.save()
        self.assertEqual(self.complete().status_code, 404)

    def test_role_crud_permissions_are_independent_and_direct_grants_do_not_bypass_role(self):
        user = get_user_model().objects.create_user(username='transfer-permissions')
        access = assign_warehouse_role(user, all_warehouses=True)
        permissions = Permission.objects.filter(content_type__app_label='warehouse', content_type__model='warehousetransfer')
        user.user_permissions.set(permissions)
        self.login(user)
        self.assertEqual(self.client.get(self.list_url).status_code, 403)
        cases = [('view', 'get', self.detail_url, {}, 200),
                 ('add', 'post', self.list_url, self.payload(), 201),
                 ('change', 'patch', self.update_url, {'notes': 'Allowed'}, 200),
                 ('delete', 'delete', self.detail_url, {}, 204)]
        for action, method, url, data, expected in cases:
            access.role.permissions.set(permissions.filter(codename=f'{action}_warehousetransfer'))
            response = getattr(self.client, method)(url, data, format='json')
            self.assertEqual(response.status_code, expected, getattr(response, 'data', None))
            if action != 'change':
                self.assertEqual(self.complete().status_code, 403)
        access.delete()
        self.assertEqual(self.client.get(self.list_url).status_code, 403)

    def test_options_pages_navigation_and_action_wiring(self):
        self.other.is_active = False; self.other.save(update_fields=['is_active'])
        for name in ['warehouse:transfer-options', 'warehouse:transfer-edit-options']:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual({row['value'] for row in response.data['source_warehouse']}, {self.source.pk, self.destination.pk})
            self.assertEqual(response.data['product'][0]['value'], self.product.pk)
        for name, args in [('warehouse_pages:transfer-list', []), ('warehouse_pages:add-transfer', []),
                           ('warehouse_pages:edit-transfer', [self.record.pk]), ('warehouse_pages:transfer-detail', [self.record.pk])]:
            page = self.client.get(reverse(name, args=args))
            self.assertEqual(page.status_code, 200)
            self.assertIn('no-store', page['Cache-Control'])
            self.assertContains(page, 'Transfer list')
        listing = self.client.get(reverse('warehouse_pages:transfer-list'))
        self.assertContains(listing, 'data-complete-id-field="transfer_id"')
        self.assertContains(listing, 'data-complete-result-key="warehouse_transfer"')
        self.assertContains(listing, 'data-edit-disabled-field="locked"')
        self.assertContains(listing, 'data-delete-disabled-field="locked"')
        transit = self.client.get(reverse('warehouse_pages:transfer-list'), {'stage': 'in_transit'})
        self.assertContains(transit, reverse('warehouse:transfer-list-view') + '?stage=in_transit')
        edit = self.client.get(reverse('warehouse_pages:edit-transfer', args=[self.record.pk]))
        self.assertContains(edit, 'data-transfer-form')
        self.assertContains(edit, 'data-transfer-add-line')
        self.assertContains(edit, f'action="{self.update_url}"')

    def test_multiple_products_and_repeated_product_lines_move_combined_quantities_once(self):
        other = Product.objects.create(name='Second item', sku='TRANSFER-SECOND', unit=self.product.unit, currency_id='BDT')
        Stock.objects.create(warehouse=self.source, product=other, quantity=7)
        response = self.client.post(self.list_url, self.payload(items=[self.line(quantity='2'), self.line(line_number=2, quantity='1'),
            self.line(line_number=3, product=other.pk, quantity='3')]), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        record = response.data['warehouse_transfer']
        self.assertEqual(record['item_count'], 3)
        self.assertEqual(len(record['items']), 3)
        response = self.complete(transfer_id=record['id'])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.balances(), {self.source.pk: Decimal('7'), self.destination.pk: Decimal('3')})
        self.assertEqual(Stock.objects.get(warehouse=self.source, product=other).quantity, Decimal('4'))
        self.assertEqual(Stock.objects.get(warehouse=self.destination, product=other).quantity, Decimal('3'))
        self.assertFalse(self.complete(transfer_id=record['id']).data['completed_now'])
        self.assertEqual(Stock.objects.filter(warehouse=self.destination).count(), 2)

    def test_shortage_on_later_product_rolls_back_every_product(self):
        other = Product.objects.create(name='Short item', sku='TRANSFER-SHORT', unit=self.product.unit, currency_id='BDT')
        Stock.objects.create(warehouse=self.source, product=other, quantity=1)
        WarehouseTransferItem.objects.create(transfer=self.record, line_number=2, product=other, unit=other.unit, quantity=2)
        response = self.complete()
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('TRANSFER-SHORT', str(response.data))
        self.assertEqual(Stock.objects.get(warehouse=self.source, product=self.product).quantity, Decimal('10'))
        self.assertEqual(Stock.objects.get(warehouse=self.source, product=other).quantity, Decimal('1'))
        self.assertFalse(Stock.objects.filter(warehouse=self.destination).exists())
        self.record.refresh_from_db(); self.assertFalse(self.record.is_completed)
        # Individual lines may fit stock while their combined quantity does not.
        self.record.items.exclude(pk=self.item.pk).delete()
        WarehouseTransferItem.objects.create(transfer=self.record, line_number=2, product=self.product, unit=self.product.unit, quantity=9)
        self.assertEqual(self.complete().status_code, 400)
        self.assertFalse(Stock.objects.filter(warehouse=self.destination).exists())

    def test_nested_edits_swap_line_numbers_preserve_ids_and_remove_or_add_lines(self):
        second = WarehouseTransferItem.objects.create(transfer=self.record, line_number=2, product=self.product,
            unit=self.product.unit, quantity=1, create_by='original-second')
        response = self.client.patch(self.update_url, {'items': [self.line(id=second.pk, line_number=1, quantity='4'),
            self.line(id=self.item.pk, line_number=2, quantity='2.5')]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([item['id'] for item in response.data['items']], [second.pk, self.item.pk])
        second.refresh_from_db(); self.assertEqual(second.create_by, 'original-second')
        self.assertEqual(second.modified_by, self.actor.username)
        response = self.client.patch(self.update_url, {'items': [self.line(id=self.item.pk), self.line(line_number=2)]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(WarehouseTransferItem.objects.filter(pk=second.pk).exists())
        self.assertEqual(self.record.items.count(), 2)
        self.assertEqual(self.balances(), {self.source.pk: Decimal('10')})

    def test_invalid_nested_items_are_atomic_and_cannot_reference_another_transfer(self):
        other = WarehouseTransfer.objects.create(source_warehouse=self.source, destination_warehouse=self.destination)
        foreign = WarehouseTransferItem.objects.create(transfer=other, line_number=1, product=self.product, unit=self.product.unit, quantity=1)
        wrong_unit = UnitOfMeasure.objects.create(name='Wrong', code='TRANSFER-WRONG')
        for items in [[], [self.line(id=foreign.pk)], [self.line(id=self.item.pk), self.line(id=self.item.pk, line_number=2)],
                      [self.line(), self.line()], [self.line(quantity='0')], [self.line(unit=wrong_unit.pk)]]:
            response = self.client.patch(self.update_url, {'notes': 'Must not save', 'items': items}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            self.record.refresh_from_db(); self.assertEqual(self.record.notes, '')
            self.assertEqual(self.record.items.count(), 1)
        self.assertEqual(self.client.post(self.list_url, self.payload(items=[self.line(id=self.item.pk)]), format='json').status_code, 400)
        self.assertEqual(self.client.patch(self.update_url, {'quantity': '2'}, format='json').status_code, 400)

    def test_failed_nested_insert_rolls_back_header_and_other_line_edits(self):
        with patch('warehouse.serializers.WarehouseTransferItem.objects.create', side_effect=IntegrityError('item failure')):
            self.assertEqual(self.client.post(self.list_url, self.payload(), format='json').status_code, 400)
            response = self.client.patch(self.update_url, {'notes': 'Wrong', 'items': [self.line(id=self.item.pk, quantity='4'),
                self.line(line_number=2)]}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(WarehouseTransfer.objects.count(), 1)
        self.item.refresh_from_db(); self.assertEqual(self.item.quantity, Decimal('2.5'))
        self.record.refresh_from_db(); self.assertEqual(self.record.notes, '')

    def test_empty_transfer_and_changed_product_units_cannot_complete(self):
        empty = WarehouseTransfer.objects.create(source_warehouse=self.source, destination_warehouse=self.destination)
        self.assertEqual(self.complete(transfer_id=empty.pk).status_code, 400)
        no_items = self.payload(); no_items.pop('items')
        self.assertEqual(self.client.post(self.list_url, no_items, format='json').status_code, 400)
        wrong_unit = UnitOfMeasure.objects.create(name='Changed', code='TRANSFER-CHANGED')
        self.product.unit = wrong_unit; self.product.save(update_fields=['unit'])
        self.assertEqual(self.complete().status_code, 400)
        self.assertEqual(self.balances(), {self.source.pk: Decimal('10')})

    def test_product_search_returns_each_transfer_once_and_header_patch_keeps_lines(self):
        WarehouseTransferItem.objects.create(transfer=self.record, line_number=2, product=self.product, unit=self.product.unit, quantity=1)
        response = self.client.get(self.list_url, {'q': self.product.sku})
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['item_count'], 2)
        response = self.client.patch(self.update_url, {'notes': 'Header only'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['item_count'], 2)
        self.assertEqual(response.data['items'][0]['id'], self.item.pk)
