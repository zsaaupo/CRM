from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounting.models import AccountTransfer, FinancialAccount
from hrm.models import Employee, LeaveRequest, LeaveType
from inventory.models import Product, UnitOfMeasure
from people.models import Party
from purchase.models import PurchaseOrder
from sale.models import SalesOrder
from warehouse.models import RoleWarehouseAccess, StockAdjustment, WareHouse, WarehouseTransfer
from warehouse.tests.helpers import assign_warehouse_role
from .models import AuditLog


User = get_user_model()


def grant(target, *permissions):
    records = []
    for permission in permissions:
        app, codename = permission.split('.')
        records.append(Permission.objects.get(content_type__app_label=app, codename=codename))
    field = target.permissions if hasattr(target, 'permissions') else target.user_permissions
    field.add(*records)


class AuditLogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(username='audit-admin', email='audit-admin@example.com')
        cls.main = WareHouse.objects.create(name='Audit main')
        cls.other = WareHouse.objects.create(name='Audit other')
        cls.unit = UnitOfMeasure.objects.create(name='Audit pieces', code='AUD-PCS')
        cls.product = Product.objects.create(sku='AUD-PROD', name='Audit product', unit=cls.unit, currency_id='BDT')

    def setUp(self):
        self.api = reverse('audit_log:api-list')
        self.page = reverse('audit_log:list')
        self.client = APIClient()

    def results(self, **query):
        response = self.client.get(self.api, query)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_menu_and_api_require_view_permission_and_expose_only_reads(self):
        self.assertEqual(self.client.get(self.page).status_code, 302)
        self.assertEqual(self.client.get(self.api).status_code, 403)
        viewer = User.objects.create_user(username='audit-viewer')
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(self.page).status_code, 403)
        self.assertEqual(self.client.get(self.api).status_code, 403)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'href="/audit-log/"')

        self.client.force_login(self.admin)
        page = self.client.get(self.page)
        self.assertContains(page, 'href="/audit-log/"')
        self.assertContains(page, 'data-api-url="/audit-log/api/"')
        self.assertNotContains(page, 'data-catalog-delete-form')
        html = page.content.decode()
        self.assertLess(html.index('data-settings-toggle'), html.index('href="/audit-log/"'))
        self.assertIn('no-store', self.client.get(self.api)['Cache-Control'])
        self.assertEqual(self.client.head(self.api).status_code, 200)
        self.assertEqual(self.client.options(self.api).status_code, 200)
        for method in ('post', 'put', 'patch', 'delete'):
            self.assertEqual(getattr(self.client, method)(self.api, {}, format='json').status_code, 405)

    def test_catalog_crud_records_actor_and_metadata_only(self):
        self.client.force_login(self.admin)
        create = reverse('inventory:product-list')
        response = self.client.post(create, {'sku': 'AUDIT-CRUD', 'name': 'Test product', 'unit': self.unit.pk,
                                            'currency': 'BDT', 'description': 'Private audit test text'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        pk = response.data['product']['id']
        detail = reverse('inventory:product-detail', args=[pk])
        self.assertEqual(self.client.patch(detail, {'name': 'Updated'}, format='json').status_code, 200)
        self.assertEqual(self.client.delete(detail).status_code, 204)
        rows = self.results(q='AUDIT-CRUD')['results']
        self.assertEqual([row['action'] for row in rows], ['deleted', 'updated', 'created'])
        self.assertTrue(all(row['actor'] == self.admin.username and row['object_id'] == str(pk) for row in rows))
        self.assertEqual(self.results(q='Private audit test text')['count'], 0)

    def test_warehouse_and_source_permissions_filter_before_search_and_pagination(self):
        for number in range(22):
            SalesOrder.objects.create(warehouse=self.main, currency_id='BDT', exchange_rate=1,
                                      order_number=f'AUD-MAIN-{number}')
        other = SalesOrder.objects.create(warehouse=self.other, currency_id='BDT', exchange_rate=1,
                                          order_number='AUD-OTHER')
        PurchaseOrder.objects.create(warehouse=self.main, currency_id='BDT', exchange_rate=1,
                                     order_number='AUD-HIDDEN-PURCHASE')
        viewer = User.objects.create_user(username='audit-branch')
        scope = assign_warehouse_role(viewer, warehouse=self.main)
        grant(scope.role, 'audit_log.view_auditlog', 'sale.view_salesorder')
        grant(viewer, 'purchase.view_purchaseorder', 'inventory.view_product')
        self.client.force_login(viewer)

        first = self.results(q='AUD-MAIN', record_type='Sales order')
        self.assertEqual(first['count'], 22)
        self.assertEqual(len(first['results']), 20)
        self.assertIsNotNone(first['next'])
        self.assertIn('record_type=Sales+order', first['next'])
        self.assertEqual(len(self.results(q='AUD-MAIN', record_type='Sales order', page=2)['results']), 2)
        self.assertEqual(self.results(q=other.order_number)['count'], 0)
        self.assertEqual(self.results(q='AUD-HIDDEN-PURCHASE')['count'], 0)
        self.assertEqual(self.results(q=self.product.sku)['count'], 1)
        self.assertTrue(all(row['warehouse_id'] == self.main.pk for row in first['results']))

        scope.all_warehouses = True
        scope.warehouse = None
        scope.save()
        self.assertEqual(self.results(q=other.order_number)['count'], 1)
        self.assertEqual(self.results(q='AUD-HIDDEN-PURCHASE')['count'], 0)

    def test_record_type_filter_shares_search_toolbar_and_uses_existing_permissions(self):
        sale = SalesOrder.objects.create(warehouse=self.main, currency_id='BDT', exchange_rate=1, order_number='AUD-FILTER')
        sale.notes = 'Edited'
        sale.save(update_fields=['notes'])
        StockAdjustment.objects.create(warehouse=self.other)
        PurchaseOrder.objects.create(warehouse=self.main, currency_id='BDT', exchange_rate=1)
        viewer = User.objects.create_user(username='audit-filter-viewer')
        scope = assign_warehouse_role(viewer, warehouse=self.main)
        grant(scope.role, 'audit_log.view_auditlog', 'sale.view_salesorder', 'warehouse.view_stockadjustment', 'inventory.view_product')
        self.client.force_login(viewer)

        page = self.client.get(self.page)
        self.assertContains(page, '<option value="">All record types</option>')
        self.assertContains(page, '<option value="Sales order">Sales order</option>', count=1)
        self.assertContains(page, '<option value="Product">Product</option>')
        self.assertNotContains(page, '<option value="Stock adjustment">')
        self.assertNotContains(page, '<option value="Purchase order">')
        toolbar = page.content.decode().split('<div class="audit-log-controls">', 1)[1].split('</div>', 1)[0]
        self.assertIn('data-catalog-search', toolbar)
        self.assertIn('name="record_type"', toolbar)
        self.assertEqual(self.results(record_type='')['count'], 3)
        self.assertEqual(self.results(record_type='Sales order')['count'], 2)
        self.assertEqual(self.results(record_type='Product', q=sale.order_number)['count'], 0)
        sales = self.results(record_type='Sales order', action='updated')['results']
        self.assertEqual([row['reference'] for row in sales], [sale.order_number])
        for record_type in ('Sales', 'Stock adjustment', 'Purchase order'):
            self.assertEqual(self.results(record_type=record_type)['count'], 0)

    def test_stock_transfers_are_visible_to_either_branch_and_financial_transfers_require_both(self):
        transfer = WarehouseTransfer.objects.create(source_warehouse=self.other, destination_warehouse=self.main)
        accounts = [FinancialAccount.objects.create(name=f'Audit Cash {branch.pk}', warehouse=branch, type='cash')
                    for branch in (self.main, self.other)]
        mixed = AccountTransfer.objects.create(source_account=accounts[0], destination_account=accounts[1], amount=1)
        same = FinancialAccount.objects.create(name='Second local account', warehouse=self.main, type='cash')
        local = AccountTransfer.objects.create(source_account=accounts[0], destination_account=same, amount=1)
        viewer = User.objects.create_user(username='audit-transfer-viewer')
        scope = assign_warehouse_role(viewer, warehouse=self.main)
        grant(scope.role, 'audit_log.view_auditlog', 'warehouse.view_warehousetransfer',
              'accounting.view_accounttransfer', 'accounting.view_financialaccount')
        self.client.force_login(viewer)
        rows = self.results(q=transfer.transfer_number)['results']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['warehouse_label'], f'{self.other.name} / {self.main.name}')
        visible_transfers = {row['object_id'] for row in self.results(q='Fund transfer')['results']}
        self.assertEqual(visible_transfers, {str(local.pk)})
        self.assertNotIn(str(mixed.pk), visible_transfers)

    def test_workflow_capture_commits_once_and_rolls_back_on_failure(self):
        self.client.force_login(self.admin)
        aid = reverse('warehouse:adjustment-list')
        complete = reverse('warehouse:adjustment-complete')
        lines = [{'product': self.product.pk, 'operation': 'subtract', 'quantity': '2'}]
        response = self.client.post(aid, {'warehouse': self.main.pk, 'items': lines}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        pk = response.data['stock_adjustment']['id']
        record = StockAdjustment.objects.get(pk=pk)
        self.assertEqual(self.client.post(complete, {'adjustment_id': pk}, format='json').status_code, 400)
        events = AuditLog.objects.filter(resource='warehouse.stockadjustment', object_id=str(pk))
        self.assertEqual(list(events.values_list('action', flat=True)), ['created'])

        item = record.items.get()
        item.operation = 'add'
        item.save(update_fields=['operation'])
        for _ in range(2):
            self.assertEqual(self.client.post(complete, {'adjustment_id': pk}, format='json').status_code, 200)
        self.assertEqual(list(events.values_list('action', flat=True)), ['completed', 'created'])
        self.assertEqual(events.first().warehouse_id, self.main.pk)

        with self.assertRaises(ValueError), transaction.atomic():
            WareHouse.objects.create(name='Rollback audit')
            raise ValueError
        self.assertFalse(WareHouse.objects.filter(name='Rollback audit').exists())
        self.assertEqual(AuditLog.objects.filter(resource='warehouse.warehouse').count(), 2)

    def test_soft_deletion_and_successful_auth_are_audited(self):
        user = User.objects.create_user(username='audit-login@example.com', email='audit-login@example.com',
                                        password='LongTestPassword#77')
        login = self.client.post(reverse('account:login'), {'email': user.email, 'password': 'LongTestPassword#77'}, format='json')
        self.assertEqual(login.status_code, 200, login.data)
        self.assertEqual(self.client.post(reverse('account:logout')).status_code, 200)
        auth_events = AuditLog.objects.filter(resource='auth.user', object_id=str(user.pk)).values_list('action', flat=True)
        self.assertEqual(list(auth_events), ['signed_out', 'signed_in', 'created'])

        party = Party.objects.create(name='Audit supplier', code='AUD-PARTY', is_supplier=True)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.delete(reverse('people:party-detail', args=[party.pk])).status_code, 204)
        event = AuditLog.objects.get(resource='people.party', object_id=str(party.pk), action='deleted')
        self.assertEqual(event.actor, self.admin.username)
        self.assertTrue(Party.all_objects.get(pk=party.pk).is_deleted)

    def test_leave_audit_matches_employee_and_reviewer_access(self):
        viewer = User.objects.create_user(username='audit-employee')
        grant(viewer, 'audit_log.view_auditlog', 'hrm.view_leaverequest')
        linked = Employee.objects.create(employee_code='AUD-EMP', full_name='Linked employee', user=viewer)
        other = Employee.objects.create(employee_code='AUD-EMP2', full_name='Other employee')
        leave_type = LeaveType.objects.create(code='AUD-LEAVE', name='Audit leave', is_paid=True)
        today = timezone.localdate()
        for employee in (linked, other):
            LeaveRequest.objects.create(employee=employee, leave_type=leave_type, start_date=today, end_date=today)
        self.client.force_login(viewer)
        self.assertEqual(self.results(q='Leave request')['count'], 1)
        grant(viewer, 'hrm.review_leaverequest')
        self.assertEqual(self.results(q='Leave request')['count'], 2)

    def test_assignment_history_follows_existing_assignment_visibility(self):
        viewer = User.objects.create_user(username='audit-assignment-manager')
        scope = assign_warehouse_role(viewer, warehouse=self.main)
        grant(scope.role, 'audit_log.view_auditlog', 'warehouse.manage_warehouse_access')
        local = RoleWarehouseAccess.objects.create(role=Group.objects.create(name='Audit local'), warehouse=self.main)
        unassigned = RoleWarehouseAccess.objects.create(role=Group.objects.create(name='Audit unassigned'))
        RoleWarehouseAccess.objects.create(role=Group.objects.create(name='Audit remote'), warehouse=self.other)
        RoleWarehouseAccess.objects.create(role=Group.objects.create(name='Audit all branches'), all_warehouses=True)
        protected = Group.objects.create(name='Audit protected role')
        self.admin.groups.add(protected)
        RoleWarehouseAccess.objects.create(role=protected, warehouse=self.main)

        self.client.force_login(viewer)
        rows = self.results(q='Role warehouse access')['results']
        self.assertEqual({row['object_id'] for row in rows}, {str(record.pk) for record in (scope, local, unassigned)})
