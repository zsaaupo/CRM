from warehouse.tests.helpers import assign_warehouse_role
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib import admin
from django.test import TestCase
from django.test import RequestFactory
from django.urls import reverse
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.test import APIClient

from inventory.models import Product, UnitOfMeasure
from purchase.models import PurchaseOrder, PurchaseOrderItem
from purchase.services import receive_purchase
from sale.models import SalesOrder, SalesOrderItem
from sale.services import complete_sale
from warehouse.models import Stock, RoleWarehouseAccess, WareHouse
from warehouse.scope import lock_warehouse_scope


class WarehouseScopeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='utt-operator')
        self.role = Group.objects.create(name="Warehouse operator")
        self.user.groups.set([self.role])
        self.role.permissions.set(Permission.objects.filter(content_type__app_label__in=['warehouse', 'purchase', 'sale']))
        self.utt = WareHouse.objects.create(name='UTT', code='UTT', address='Dhaka', phone='1')
        self.other = WareHouse.objects.create(name='Other', code='OTHER', address='Dhaka', phone='2')
        self.scope = RoleWarehouseAccess.objects.create(role=self.role, warehouse=self.utt)
        self.unit = UnitOfMeasure.objects.create(name='Pieces', code='SCOPE-PCS')
        self.product = Product.objects.create(name='Rice', sku='SCOPE-RICE', unit=self.unit, currency_id='BDT')
        self.stocks = [Stock.objects.create(warehouse=warehouse, product=self.product, quantity=20)
                       for warehouse in [self.utt, self.other]]
        self.purchases = [self.order(PurchaseOrder, PurchaseOrderItem, 'purchase_order', warehouse)
                          for warehouse in [self.utt, self.other, None]]
        self.sales = [self.order(SalesOrder, SalesOrderItem, 'sales_order', warehouse)
                      for warehouse in [self.utt, self.other, None]]
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)

    def line(self):
        return {'line_number': 1, 'product': self.product.pk, 'unit': self.unit.pk, 'quantity': '3', 'unit_price': '1'}

    def order(self, model, item_model, parent, warehouse):
        order = model.objects.create(warehouse=warehouse, currency_id='BDT', exchange_rate=1)
        line = self.line()
        line['product_id'] = line.pop('product')
        line['unit_id'] = line.pop('unit')
        item_model.objects.create(**{parent: order}, **line)
        return order

    def resources(self):
        return [('purchase', 'purchase-order', self.purchases), ('sale', 'sales-order', self.sales)]

    def test_lists_details_search_pagination_and_item_filters_only_return_assigned_warehouse(self):
        for app, route, orders in self.resources():
            for name in ['list', 'list-view']:
                response = self.client.get(reverse(f'{app}:{route}-{name}'))
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data['count'], 1)
                self.assertEqual(response.data['results'][0]['id'], orders[0].pk)
            for order in orders[1:]:
                self.assertEqual(self.client.get(reverse(f'{app}:{route}-detail', args=[order.pk])).status_code, 404)
            items = self.client.get(reverse(f'{app}:{route}-item-list-view'))
            self.assertEqual(items.data['count'], 1)
            self.assertEqual(items.data['results'][0]['id'], orders[0].items.get().pk)
            foreign = self.client.get(reverse(f'{app}:{route}-item-list-view'), {route.replace('-', '_'): orders[1].pk})
            self.assertEqual(foreign.data['count'], 0)
            self.assertEqual(self.client.get(reverse(f'{app}:{route}-list-view'), {'q': orders[1].order_number}).data['count'], 0)
        stocks = self.client.get(reverse('warehouse:stock-list'))
        self.assertEqual(stocks.data['count'], 1)
        self.assertEqual(stocks.data['results'][0]['id'], self.stocks[0].pk)
        self.assertEqual(self.client.get(reverse('warehouse:stock-list'), {'warehouse': self.other.pk}).data['count'], 0)
        self.assertEqual(self.client.get(reverse('warehouse:stock-detail', args=[self.stocks[1].pk])).status_code, 404)
        warehouses = self.client.get(reverse('warehouse:warehouse-list'))
        self.assertEqual([row['id'] for row in warehouses.data['results']], [self.utt.pk])

    def test_creation_defaults_to_assignment_and_rejects_null_or_other_warehouse(self):
        for app, route, orders in self.resources():
            url = reverse(f'{app}:{route}-list')
            payload = {'currency': 'BDT', 'exchange_rate': '1', 'items': [self.line()]}
            response = self.client.post(url, payload, format='json')
            self.assertEqual(response.status_code, 201, response.data)
            record = response.data[route.replace('-', '_')]
            self.assertEqual(record['warehouse'], self.utt.pk)
            for warehouse in [self.other.pk, None]:
                response = self.client.post(url, {**payload, 'warehouse': warehouse}, format='json')
                self.assertEqual(response.status_code, 403, response.data)

    def test_header_update_delete_and_warehouse_changes_are_scoped(self):
        for app, route, orders in self.resources():
            own = reverse(f'{app}:{route}-update', args=[orders[0].pk])
            self.assertEqual(self.client.patch(own, {'notes': 'UTT'}, format='json').status_code, 200)
            for warehouse in [self.other.pk, None]:
                self.assertEqual(self.client.patch(own, {'warehouse': warehouse}, format='json').status_code, 403)
            for method, suffix, payload in [('patch', 'detail', {'notes': 'No'}), ('patch', 'update', {}),
                                            ('put', 'update', {'currency': 'BDT', 'exchange_rate': 1}), ('delete', 'detail', {})]:
                url = reverse(f'{app}:{route}-{suffix}', args=[orders[1].pk])
                self.assertEqual(getattr(self.client, method)(url, payload, format='json').status_code, 404)
            self.assertEqual(self.client.delete(reverse(f'{app}:{route}-detail', args=[orders[0].pk])).status_code, 204)

    def test_individual_items_cannot_read_write_or_reparent_across_scope(self):
        for app, route, orders in self.resources():
            parent = route.replace('-', '_')
            own_item = orders[0].items.get()
            foreign_item = orders[1].items.get()
            payload = {**self.line(), parent: orders[1].pk, 'line_number': 2}
            response = self.client.post(reverse(f'{app}:{route}-item-list'), payload, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            own = reverse(f'{app}:{route}-item-update', args=[own_item.pk])
            self.assertEqual(self.client.patch(own, {parent: orders[1].pk}, format='json').status_code, 400)
            self.assertEqual(self.client.patch(own, {'quantity': 4}, format='json').status_code, 200)
            for method, suffix in [('get', 'detail'), ('patch', 'update'), ('delete', 'detail')]:
                url = reverse(f'{app}:{route}-item-{suffix}', args=[foreign_item.pk])
                self.assertEqual(getattr(self.client, method)(url, {}, format='json').status_code, 404)
            nested = {'items': [{'id': foreign_item.pk, **self.line()}]}
            response = self.client.patch(reverse(f'{app}:{route}-update', args=[orders[0].pk]), nested, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            self.assertTrue(orders[0].items.filter(pk=own_item.pk).exists())

    def test_receive_and_complete_check_scope_in_api_and_services_before_stock_changes(self):
        for service, order in [(receive_purchase, self.purchases[1]), (complete_sale, self.sales[1])]:
            with self.assertRaises(NotFound):
                service(self.user, order.pk)
        for route, key, flag, order in [
            ('purchase:purchase-order-receive', 'purchase_id', 'is_received', self.purchases[1]),
            ('sale:sales-order-complete', 'sale_id', 'is_completed', self.sales[1]),
        ]:
            response = self.client.post(reverse(route), {key: order.pk, flag: True}, format='json')
            self.assertEqual(response.status_code, 404, response.data)
            order.refresh_from_db()
            self.assertFalse(getattr(order, flag))
        self.assertEqual(list(Stock.objects.order_by('pk').values_list('quantity', flat=True)), [Decimal('20'), Decimal('20')])
        self.assertTrue(receive_purchase(self.user, self.purchases[0].pk)[1])
        self.assertFalse(receive_purchase(self.user, self.purchases[0].pk)[1])
        self.assertTrue(complete_sale(self.user, self.sales[0].pk)[1])
        self.assertFalse(complete_sale(self.user, self.sales[0].pk)[1])
        self.assertEqual(Stock.objects.get(pk=self.stocks[0].pk).quantity, Decimal('20'))
        self.assertEqual(Stock.objects.get(pk=self.stocks[1].pk).quantity, Decimal('20'))

    def test_dropdowns_are_scoped_and_do_not_require_warehouse_view_permission(self):
        self.role.permissions.remove(*Permission.objects.filter(content_type__app_label='warehouse'))
        for route in ['purchase:purchase-options', 'purchase:purchase-edit-options', 'sale:sale-options', 'sale:sales-edit-options']:
            response = self.client.get(reverse(route))
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual([row['value'] for row in response.data['warehouse']], [self.utt.pk])
            self.assertEqual(response.data['warehouse_scope'], {'warehouse': self.utt.pk, 'all_warehouses': False})

    def test_unassigned_missing_and_revoked_scope_deny_apis_and_pages(self):
        for delete in [False, True]:
            if delete:
                self.scope.delete()
            else:
                self.scope.warehouse = None
                self.scope.save()
            for route in ['warehouse:warehouse-list', 'warehouse:stock-list', 'purchase:purchase-order-list',
                          'sale:sales-order-list', 'purchase:purchase-options', 'sale:sale-options']:
                self.assertEqual(self.client.get(reverse(route)).status_code, 403)
            for route in ['warehouse_pages:warehouse-list', 'warehouse_pages:stock-list',
                          'purchase_pages:purchase-list', 'sale_pages:sale-list']:
                self.assertEqual(self.client.get(reverse(route)).status_code, 403)
            with self.assertRaises(PermissionDenied):
                receive_purchase(self.user, self.purchases[0].pk)
            self.assertNotContains(self.client.get(reverse('dashboard')), 'data-sales-toggle')

    def test_all_warehouses_and_superuser_bypass_scope_but_not_model_permissions(self):
        self.scope.warehouse = None
        self.scope.all_warehouses = True
        self.scope.save()
        for app, route, orders in self.resources():
            self.assertEqual(self.client.get(reverse(f'{app}:{route}-list-view')).data['count'], 3)
            response = self.client.patch(reverse(f'{app}:{route}-update', args=[orders[0].pk]),
                {'warehouse': self.other.pk}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
        self.role.permissions.set(Permission.objects.filter(content_type__app_label__in=['warehouse', 'purchase', 'sale'],
            codename__startswith='view_'))
        for app, route, orders in self.resources():
            self.assertEqual(self.client.post(reverse(f'{app}:{route}-list'), {}, format='json').status_code, 403)
            self.assertEqual(self.client.delete(reverse(f'{app}:{route}-detail', args=[orders[1].pk])).status_code, 403)
        root = get_user_model().objects.create_superuser(username='scope-root', email='scope@example.com')
        self.client.force_login(root)
        self.assertEqual(self.client.get(reverse('sale:sales-order-list-view')).data['count'], 3)

    def test_warehouse_crud_requires_all_access_for_create_and_scope_for_existing(self):
        payload = {'name': 'New', 'address': 'Dhaka', 'phone': '3'}
        self.assertEqual(self.client.post(reverse('warehouse:warehouse-list'), payload, format='json').status_code, 403)
        self.assertEqual(self.client.get(reverse('warehouse_pages:add-warehouse')).status_code, 403)
        for method in ['get', 'patch', 'delete']:
            self.assertEqual(getattr(self.client, method)(reverse('warehouse:warehouse-detail', args=[self.other.pk]),
                payload, format='json').status_code, 404)
        self.assertEqual(self.client.patch(reverse('warehouse:warehouse-update', args=[self.utt.pk]),
            {'phone': 'updated'}, format='json').status_code, 200)
        self.scope.warehouse = None
        self.scope.all_warehouses = True
        self.scope.save()
        self.assertEqual(self.client.post(reverse('warehouse:warehouse-list'), payload, format='json').status_code, 201)

    def test_reassignment_takes_effect_on_next_request_without_login(self):
        self.assertEqual(self.client.get(reverse('warehouse:stock-list')).data['results'][0]['id'], self.stocks[0].pk)
        RoleWarehouseAccess.objects.filter(pk=self.scope.pk).update(warehouse=self.other)
        self.assertEqual(self.client.get(reverse('warehouse:stock-list')).data['results'][0]['id'], self.stocks[1].pk)
        self.assertEqual(self.client.get(reverse('purchase:purchase-order-detail', args=[self.purchases[0].pk])).status_code, 404)

    def test_read_only_permissions_with_specific_scope_never_grant_writes(self):
        self.role.permissions.set(Permission.objects.filter(content_type__app_label__in=['warehouse', 'purchase', 'sale'],
            codename__in=['view_stock', 'view_purchaseorder', 'view_salesorder']))
        self.assertEqual(self.client.get(reverse('warehouse:stock-list')).data['count'], 1)
        for app, route, orders in self.resources():
            self.assertEqual(self.client.get(reverse(f'{app}:{route}-list-view')).data['count'], 1)
            for method, url, payload in [
                ('post', reverse(f'{app}:{route}-list'), {'currency': 'BDT', 'exchange_rate': 1}),
                ('patch', reverse(f'{app}:{route}-update', args=[orders[0].pk]), {'notes': 'No'}),
                ('delete', reverse(f'{app}:{route}-detail', args=[orders[0].pk]), {}),
            ]:
                self.assertEqual(getattr(self.client, method)(url, payload, format='json').status_code, 403)

    def test_pagination_counts_and_next_links_do_not_include_other_warehouses(self):
        PurchaseOrder.objects.bulk_create([PurchaseOrder(warehouse=self.other, currency_id='BDT', exchange_rate=1)
                                          for _ in range(25)])
        response = self.client.get(reverse('purchase:purchase-order-list-view'))
        self.assertEqual(response.data['count'], 1)
        self.assertIsNone(response.data['next'])
        self.assertEqual(self.client.get(reverse('purchase:purchase-order-list-view'), {'page': 2}).status_code, 404)

    def test_admin_queries_and_warehouse_choices_follow_the_same_scope(self):
        request = RequestFactory().get('/admin/')
        request.user = self.user
        warehouse_admin = admin.site._registry[WareHouse]
        stock_admin = admin.site._registry[Stock]
        self.assertEqual(list(warehouse_admin.get_queryset(request).values_list('pk', flat=True)), [self.utt.pk])
        self.assertEqual(list(stock_admin.get_queryset(request).values_list('pk', flat=True)), [self.stocks[0].pk])
        self.assertFalse(warehouse_admin.has_add_permission(request))
        field = stock_admin.formfield_for_foreignkey(Stock._meta.get_field('warehouse'), request)
        self.assertEqual(list(field.queryset.values_list('pk', flat=True)), [self.utt.pk])
        RoleWarehouseAccess.objects.filter(pk=self.scope.pk).update(warehouse=None)
        self.assertFalse(stock_admin.get_queryset(request).exists())
        self.assertFalse(stock_admin.has_add_permission(request))

    def test_role_members_inherit_scope_and_scope_changes_together(self):
        colleague = get_user_model().objects.create_user(username='same-role-member')
        colleague.groups.set([self.role])
        for user in [self.user, colleague]:
            self.client.force_login(user)
            response = self.client.get(reverse('warehouse:stock-list'))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['results'][0]['id'], self.stocks[0].pk)
        RoleWarehouseAccess.objects.filter(role=self.role).update(warehouse=self.other)
        for user in [self.user, colleague]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('warehouse:stock-list')).data['results'][0]['id'], self.stocks[1].pk)

    def test_role_change_is_immediate_and_direct_user_permissions_cannot_bypass_role(self):
        self.user.user_permissions.set(Permission.objects.filter(content_type__app_label__in=['warehouse', 'purchase', 'sale']))
        self.role.permissions.clear()
        self.assertEqual(self.client.get(reverse('warehouse:stock-list')).status_code, 403)
        other_role = Group.objects.create(name='Other warehouse reader')
        other_role.permissions.add(Permission.objects.get(content_type__app_label='warehouse', codename='view_stock'))
        RoleWarehouseAccess.objects.create(role=other_role, warehouse=self.other)
        self.user.groups.set([other_role])
        response = self.client.get(reverse('warehouse:stock-list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['results'][0]['id'], self.stocks[1].pk)
        self.user.groups.add(self.role)
        self.assertEqual(self.client.get(reverse('warehouse:stock-list')).status_code, 403)
        self.user.groups.clear()
        self.assertEqual(self.client.get(reverse('warehouse:stock-list')).status_code, 403)

    def test_role_assignment_cannot_grant_warehouse_access_beyond_the_actor_scope(self):
        self.role.permissions.add(*Permission.objects.filter(codename__in=[
            'view_user', 'change_user', 'view_group', 'manage_access',
        ]))
        target = get_user_model().objects.create_user(username='role-assignment-target')
        global_role = Group.objects.create(name='All warehouse role')
        RoleWarehouseAccess.objects.create(role=global_role, all_warehouses=True)
        for url, method, payload in [
            (reverse('account:assign-group'), 'post', {'user': target.pk, 'group': global_role.pk}),
            (reverse('account:user-groups', args=[target.pk]), 'put', {'group': global_role.pk}),
        ]:
            response = getattr(self.client, method)(url, payload, format='json')
            self.assertEqual(response.status_code, 403, response.data)
            self.assertFalse(target.groups.exists())
        allowed = Group.objects.create(name='UTT colleague role')
        RoleWarehouseAccess.objects.create(role=allowed, warehouse=self.utt)
        response = self.client.put(reverse('account:user-groups', args=[target.pk]), {'group': allowed.pk}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(target.groups.get(), allowed)

    def test_stock_actions_recheck_role_permissions_after_role_membership_changes(self):
        restricted_role = Group.objects.create(name='UTT read-only replacement')
        RoleWarehouseAccess.objects.create(role=restricted_role, warehouse=self.utt)
        def change_role_before_lock(actor):
            actor.groups.set([restricted_role])
            return lock_warehouse_scope(actor)
        for module, service, order in [('purchase.services', receive_purchase, self.purchases[0]),
                                       ('sale.services', complete_sale, self.sales[0])]:
            self.user.groups.set([self.role])
            with patch(module + '.lock_warehouse_scope', side_effect=change_role_before_lock):
                with self.assertRaises(PermissionDenied):
                    service(self.user, order.pk)
        self.assertEqual(list(Stock.objects.order_by('pk').values_list('quantity', flat=True)), [Decimal('20'), Decimal('20')])
