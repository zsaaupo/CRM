from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class RoleWarehouseAccessMigrationTests(TransactionTestCase):
    before = [('warehouse', '0003_user_warehouse_access')]
    after = [('warehouse', '0004_role_warehouse_access')]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def restore(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def fixture(self):
        self.addCleanup(self.restore)
        apps = self.migrate(self.before)
        Group = apps.get_model('auth', 'Group')
        User = apps.get_model(settings.AUTH_USER_MODEL)
        role = Group.objects.create(name='Migrated warehouse role')
        first = User.objects.create(username='first-member')
        second = User.objects.create(username='second-member')
        first.groups.add(role)
        second.groups.add(role)
        warehouse = apps.get_model('warehouse', 'WareHouse').objects.create(name='UTT', address='Dhaka', phone='1')
        return apps, role, first, second, warehouse

    def test_matching_user_scopes_merge_into_role_and_permission_grants_are_preserved(self):
        old, role, first, second, warehouse = self.fixture()
        OldAccess = old.get_model('warehouse', 'UserWarehouseAccess')
        original = OldAccess.objects.create(user=first, warehouse=warehouse, create_by='creator', modified_by='editor')
        OldAccess.objects.create(user=second, warehouse=warehouse)
        permission = old.get_model('auth', 'Permission').objects.get(
            content_type__app_label='warehouse', codename='change_userwarehouseaccess')
        role.permissions.add(permission)
        new = self.migrate(self.after)
        access = new.get_model('warehouse', 'RoleWarehouseAccess').objects.get()
        self.assertEqual(access.role_id, role.pk)
        self.assertEqual(access.warehouse_id, warehouse.pk)
        for field in ['create_by', 'modified_by', 'created_at', 'updated_at']:
            self.assertEqual(getattr(access, field), getattr(original, field))
        migrated_role = new.get_model('auth', 'Group').objects.get(pk=role.pk)
        self.assertTrue(migrated_role.permissions.filter(codename='change_rolewarehouseaccess').exists())
        self.assertNotIn('warehouse_userwarehouseaccess', connection.introspection.table_names())
        connection.check_constraints()
        restored = self.migrate(self.before)
        self.assertEqual(restored.get_model('warehouse', 'UserWarehouseAccess').objects.filter(warehouse_id=warehouse.pk).count(), 2)
        connection.check_constraints()

    def test_conflicting_user_scopes_block_instead_of_granting_a_broader_role_scope(self):
        old, role, first, second, warehouse = self.fixture()
        OldAccess = old.get_model('warehouse', 'UserWarehouseAccess')
        OldAccess.objects.create(user=first, warehouse=warehouse)
        OldAccess.objects.create(user=second, all_warehouses=True)
        with self.assertRaisesRegex(RuntimeError, 'different warehouse access'):
            self.migrate(self.after)
        self.assertEqual(OldAccess.objects.count(), 2)
        self.assertNotIn('warehouse_rolewarehouseaccess', connection.introspection.table_names())
        OldAccess.objects.update(all_warehouses=False, warehouse_id=warehouse.pk)

    def test_member_without_user_assignment_is_not_silently_granted_access(self):
        old, role, first, second, warehouse = self.fixture()
        OldAccess = old.get_model('warehouse', 'UserWarehouseAccess')
        OldAccess.objects.create(user=first, warehouse=warehouse)
        with self.assertRaisesRegex(RuntimeError, 'different warehouse access'):
            self.migrate(self.after)
        self.assertEqual(OldAccess.objects.count(), 1)
        OldAccess.objects.create(user=second, warehouse=warehouse)
