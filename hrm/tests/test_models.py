from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from account.models import Account
from common.models import ProductStatus, TradeStatus
from hrm.models import Department, Designation, Employee, EmployeeStatus, EmploymentType
from people.models import Party
from warehouse.models import RoleWarehouseAccess, WareHouse


class EmployeeModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.department = Department.objects.create(code='operations', name='Operations')
        cls.designation = Designation.objects.create(code='manager', name='Manager')
        cls.warehouse = WareHouse.objects.create(name='UTT', address='Uttara', phone='123', code='UTT')

    def employee(self, code='EMP-001', **kwargs):
        values = {'employee_code': code, 'full_name': 'Employee Name', 'create_by': 'tester', 'modified_by': 'tester'}
        values.update(kwargs)
        return Employee(**values)

    def test_employee_without_login_uses_seeded_defaults_without_creating_other_identities(self):
        counts = (get_user_model().objects.count(), Account.objects.count(), Party.all_objects.count())
        employee = self.employee()
        employee.full_clean()
        employee.save()
        self.assertIsNone(employee.user_id)
        self.assertEqual(employee.employment_type.code, 'full-time')
        self.assertEqual(employee.status.code, 'active')
        self.assertEqual(counts, (get_user_model().objects.count(), Account.objects.count(), Party.all_objects.count()))
        self.assertEqual(set(EmploymentType.objects.values_list('code', flat=True)),
                         {'full-time', 'part-time', 'contract', 'temporary', 'internship'})
        self.assertEqual(set(EmployeeStatus.objects.values_list('code', flat=True)),
                         {'active', 'on-leave', 'suspended', 'left'})

    def test_login_link_is_optional_unique_and_protected_after_archiving(self):
        self.employee().save()
        self.employee('EMP-002').save()
        user = get_user_model().objects.create_user(username='employee')
        linked = self.employee('EMP-003', user=user)
        linked.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.employee('EMP-004', user=user).save()
        linked.delete(actor='tester')
        replacement = self.employee('EMP-005', user=user)
        with self.assertRaises(ValidationError) as error:
            replacement.full_clean()
        self.assertIn('user', error.exception.message_dict)
        with self.assertRaises(ProtectedError):
            user.delete()
        user.refresh_from_db()
        self.assertEqual(user.employee.pk, linked.pk)

    def test_normalizes_employee_identity_and_reserves_archived_codes(self):
        employee = self.employee(' emp-001 ', full_name=' Employee Name ', work_email='WORK@EXAMPLE.COM')
        employee.full_clean()
        self.assertEqual((employee.employee_code, employee.full_name, employee.work_email),
                         ('EMP-001', 'Employee Name', 'work@example.com'))
        employee.save()
        employee.delete(actor='tester')
        duplicate = self.employee('emp-001')
        with self.assertRaises(ValidationError) as error:
            duplicate.full_clean()
        self.assertIn('employee_code', error.exception.message_dict)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.employee(' emp-001 ').save()

    def test_whitespace_only_employee_identity_is_invalid(self):
        for field in ['employee_code', 'full_name']:
            with self.subTest(field=field), self.assertRaises(ValidationError) as error:
                self.employee(**{field: '   '}).full_clean()
            self.assertIn(field, error.exception.message_dict)

    def test_leaving_date_is_checked_by_validation_and_database(self):
        employee = self.employee(joining_date=date(2026, 5, 10), leaving_date=date(2026, 5, 9))
        with self.assertRaises(ValidationError) as error:
            employee.full_clean()
        self.assertIn('leaving_date', error.exception.message_dict)
        with self.assertRaises(IntegrityError), transaction.atomic():
            employee.save()
        employee.leaving_date = employee.joining_date
        employee.full_clean()
        employee.save()

    def test_self_reporting_is_checked_by_validation_and_database(self):
        employee = self.employee()
        employee.save()
        employee.reports_to = employee
        with self.assertRaises(ValidationError) as error:
            employee.full_clean()
        self.assertIn('reports_to', error.exception.message_dict)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Employee.objects.filter(pk=employee.pk).update(reports_to=employee)

    def test_reporting_cycles_include_archived_managers(self):
        head = self.employee('EMP-001')
        head.save()
        manager = self.employee('EMP-002', reports_to=head)
        manager.save()
        report = self.employee('EMP-003', reports_to=manager)
        report.full_clean()
        report.save()
        manager.delete(actor='tester')
        head.reports_to = report
        with self.assertRaises(ValidationError) as error:
            head.full_clean()
        self.assertIn('reports_to', error.exception.message_dict)
        self.assertEqual(report.reports_to.pk, manager.pk)

    def test_workplace_and_employment_history_references_are_protected(self):
        employee = self.employee(department=self.department, designation=self.designation, warehouse=self.warehouse)
        employee.save()
        employee.delete(actor='tester')
        for referenced in [self.department, self.designation, self.warehouse, employee.status, employee.employment_type]:
            with self.subTest(model=type(referenced).__name__), self.assertRaises(ProtectedError):
                referenced.delete()

    def test_instance_archive_is_idempotent_and_keeps_audit(self):
        employee = self.employee()
        employee.save()
        self.assertEqual(employee.delete(actor='hr-manager')[0], 1)
        archived_at = employee.archived_at
        self.assertFalse(Employee.objects.filter(pk=employee.pk).exists())
        self.assertTrue(Employee.all_objects.filter(pk=employee.pk).exists())
        self.assertTrue(employee.is_archived)
        self.assertEqual(employee.archived_by, 'hr-manager')
        self.assertEqual(employee.modified_by, 'hr-manager')
        self.assertEqual(employee.status_id, 'active')
        self.assertEqual(employee.delete(actor='another-manager')[0], 0)
        employee.refresh_from_db()
        self.assertEqual(employee.archived_at, archived_at)
        self.assertEqual(employee.archived_by, 'hr-manager')

    def test_queryset_archive_keeps_records_and_excludes_already_archived(self):
        first = self.employee()
        first.save()
        second = self.employee('EMP-002')
        second.save()
        first.delete(actor='first-manager')
        self.assertEqual(Employee.all_objects.all().delete(actor='second-manager')[0], 1)
        self.assertEqual(Employee.objects.count(), 0)
        self.assertEqual(Employee.all_objects.count(), 2)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.archived_by, 'first-manager')
        self.assertEqual(second.archived_by, 'second-manager')

    def test_employment_changes_do_not_change_login_roles_profile_or_warehouse_scope(self):
        user = get_user_model().objects.create_user(username='warehouse-staff', is_active=True)
        role = Group.objects.create(name='Warehouse staff')
        user.groups.add(role)
        permission = Permission.objects.get(content_type__app_label='warehouse', codename='view_stock')
        role.permissions.add(permission)
        access = RoleWarehouseAccess.objects.create(role=role, warehouse=self.warehouse)
        account = Account.objects.create(user=user, full_name='Application Name', email='profile@example.com')
        worksite = WareHouse.objects.create(name='DHK', code='DHK', address='Dhaka', phone='456')
        employee = self.employee(user=user, warehouse=worksite, status_id='left',
                                 leaving_date=date.today() + timedelta(days=1))
        employee.full_clean()
        employee.save()
        employee.delete(actor='hr-manager')
        user.refresh_from_db()
        access.refresh_from_db()
        account.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertEqual(list(user.groups.values_list('pk', flat=True)), [role.pk])
        self.assertEqual(list(role.permissions.values_list('pk', flat=True)), [permission.pk])
        self.assertEqual(access.warehouse_id, self.warehouse.pk)
        self.assertFalse(access.all_warehouses)
        self.assertEqual(account.full_name, 'Application Name')


class HRMLookupTests(TestCase):
    def test_lookup_codes_are_normalized_and_case_insensitively_unique(self):
        for model in [Department, Designation, EmploymentType]:
            with self.subTest(model=model.__name__):
                lookup = model(code=' CUSTOM ', name=' Custom ', create_by='tester', modified_by='tester')
                lookup.full_clean()
                self.assertEqual((lookup.code, lookup.name), ('custom', 'Custom'))
                lookup.save()
                with self.assertRaises(IntegrityError), transaction.atomic():
                    model.objects.create(code=' Custom ', name='Duplicate')

    def test_lookup_whitespace_only_identity_is_invalid(self):
        for field in ['code', 'name']:
            values = {'code': 'new', 'name': 'New', 'create_by': 'tester', 'modified_by': 'tester', field: '   '}
            with self.subTest(field=field), self.assertRaises(ValidationError) as error:
                Department(**values).full_clean()
            self.assertIn(field, error.exception.message_dict)

    def test_employee_statuses_and_employment_types_are_separate_configurable_tables(self):
        status = EmployeeStatus.objects.create(code='probation', name='Probation')
        employment_type = EmploymentType.objects.create(code='seasonal', name='Seasonal')
        employee = Employee(employee_code='EMP-001', full_name='Seasonal Employee',
                            status=status, employment_type=employment_type, create_by='tester', modified_by='tester')
        employee.full_clean()
        employee.save()
        self.assertEqual(employee.status_id, 'probation')
        self.assertEqual(employee.employment_type_id, 'seasonal')
        self.assertNotEqual(EmployeeStatus._meta.db_table, EmploymentType._meta.db_table)

    def test_employee_status_uses_shared_slug_validation_and_unique_codes(self):
        for code in ['not a slug', 'x' * 33]:
            with self.subTest(code=code), self.assertRaises(ValidationError) as error:
                EmployeeStatus(code=code, name='Invalid', create_by='tester', modified_by='tester').full_clean()
            self.assertIn('code', error.exception.message_dict)
        with self.assertRaises(IntegrityError), transaction.atomic():
            EmployeeStatus.objects.create(code='active', name='Duplicate')

    def test_employee_status_records_are_independent_of_product_and_trade_statuses(self):
        product_status = ProductStatus.objects.create(code='hrm-test', name='Product state')
        trade_status = TradeStatus.objects.create(code='hrm-test', name='Trade state')
        employee_status = EmployeeStatus.objects.create(code='hrm-test', name='Employee state')
        employee_status.name = 'Updated employee state'
        employee_status.full_clean(exclude=['create_by', 'modified_by'])
        employee_status.save()
        product_status.refresh_from_db()
        trade_status.refresh_from_db()
        self.assertEqual(product_status.name, 'Product state')
        self.assertEqual(trade_status.name, 'Trade state')
