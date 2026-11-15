from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from warehouse.models import RoleWarehouseAccess, WareHouse


class RoleWarehouseAccessModelTests(TestCase):
    def setUp(self):
        self.role = Group.objects.create(name='Warehouse role')
        self.warehouse = WareHouse.objects.create(name='UTT', address='Dhaka', phone='1', code='UTT')

    def test_default_configuration_is_unassigned_and_keeps_audit_fields(self):
        access = RoleWarehouseAccess(role=self.role, create_by='admin', modified_by='admin')
        access.full_clean()
        access.save()
        access.refresh_from_db()
        self.assertFalse(access.all_warehouses)
        self.assertIsNone(access.warehouse_id)
        self.assertEqual(self.role.warehouse_access, access)
        self.assertIn('No warehouse access', str(access))
        self.assertEqual(access.create_by, 'admin')
        self.assertIsNotNone(access.created_at)
        self.assertIsNotNone(access.updated_at)

    def test_specific_warehouse_and_all_warehouses_are_valid_separate_modes(self):
        access = RoleWarehouseAccess(role=self.role, warehouse=self.warehouse, create_by='admin', modified_by='admin')
        access.full_clean()
        access.save()
        self.assertIn('UTT', str(access))
        access.warehouse = None
        access.all_warehouses = True
        access.full_clean()
        access.save()
        access.refresh_from_db()
        self.assertTrue(access.all_warehouses)
        self.assertIsNone(access.warehouse_id)
        self.assertIn('All warehouses', str(access))
        self.assertFalse(self.role.permissions.exists())

    def test_combining_access_modes_is_rejected_by_validation_and_database(self):
        access = RoleWarehouseAccess(role=self.role, warehouse=self.warehouse, all_warehouses=True,
                                     create_by='admin', modified_by='admin')
        with self.assertRaises(ValidationError) as error:
            access.full_clean()
        self.assertIn('warehouse', error.exception.message_dict)
        with self.assertRaises(IntegrityError), transaction.atomic():
            access.save()
        self.assertFalse(RoleWarehouseAccess.objects.exists())

    def test_one_assignment_per_role_but_multiple_roles_can_share_a_warehouse(self):
        RoleWarehouseAccess.objects.create(role=self.role, warehouse=self.warehouse)
        with self.assertRaises(IntegrityError), transaction.atomic():
            RoleWarehouseAccess.objects.create(role=self.role, all_warehouses=True)
        other = Group.objects.create(name='Another role')
        RoleWarehouseAccess.objects.create(role=other, warehouse=self.warehouse)
        self.assertEqual(self.warehouse.role_access_assignments.count(), 2)

    def test_assigned_warehouse_is_protected_from_deletion(self):
        RoleWarehouseAccess.objects.create(role=self.role, warehouse=self.warehouse)
        with self.assertRaises(ProtectedError):
            self.warehouse.delete()

    def test_deleting_role_cleans_up_assignment_and_keeps_warehouse(self):
        RoleWarehouseAccess.objects.create(role=self.role, warehouse=self.warehouse)
        self.role.delete()
        self.assertFalse(RoleWarehouseAccess.objects.exists())
        self.assertTrue(WareHouse.objects.filter(pk=self.warehouse.pk).exists())
