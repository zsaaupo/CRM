from decimal import Decimal

from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounting.models import AccountTransaction, AccountTransfer, ExpensePayment, PurchasePayment, SalesPayment
from hrm.models import (
    Employee, EmployeeLoan, LeaveRequest, LeaveType, PayrollPeriod, PayrollRun, Payslip, PayslipLine,
    SalaryPayment,
)
from sale.models import SalesOrder, SalesReturn
from warehouse.models import RoleWarehouseAccess, WareHouse
from warehouse.tests.helpers import assign_warehouse_role


class ProjectAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.root = get_user_model().objects.create_superuser(username='project-admin')
        cls.staff = get_user_model().objects.create_user(username='project-staff', is_staff=True)

    def grant(self, recipient, app_label, *codenames):
        recipient.add(*Permission.objects.filter(content_type__app_label=app_label, codename__in=codenames))

    def test_all_project_models_have_admin_lists(self):
        self.client.force_login(self.root)
        posted_models = (AccountTransaction, AccountTransfer, ExpensePayment, PurchasePayment, SalesPayment)
        for app_label in ('account', 'accounting', 'common', 'hrm', 'inventory', 'people', 'purchase', 'report', 'sale', 'warehouse'):
            for model in apps.get_app_config(app_label).get_models():
                with self.subTest(model=model._meta.label):
                    self.assertTrue(admin.site.is_registered(model))
                    url = reverse(f'admin:{app_label}_{model._meta.model_name}_changelist')
                    self.assertEqual(self.client.get(url).status_code, 200)
                    add = reverse(f'admin:{app_label}_{model._meta.model_name}_add')
                    expected = 403 if model in posted_models else 200
                    self.assertEqual(self.client.get(add).status_code, expected)

    def test_leave_request_visibility_follows_reviewer_permissions(self):
        leave_type = LeaveType.objects.create(code='personal-test', name='Personal', is_paid=True)
        today = timezone.localdate()
        employee = Employee.objects.create(employee_code='TEST-OWN', full_name='Own Employee', user=self.staff)
        colleague = Employee.objects.create(employee_code='TEST-OTHER', full_name='Other Employee')
        own_leave = LeaveRequest.objects.create(employee=employee, leave_type=leave_type, start_date=today, end_date=today)
        other_leave = LeaveRequest.objects.create(employee=colleague, leave_type=leave_type, start_date=today, end_date=today)
        url = reverse('admin:hrm_leaverequest_changelist')
        self.client.force_login(self.staff)
        self.grant(self.staff.user_permissions, 'hrm', 'view_leaverequest')
        self.assertEqual(list(self.client.get(url).context['cl'].result_list), [own_leave])
        self.grant(self.staff.user_permissions, 'hrm', 'review_leaverequest')
        self.assertCountEqual(self.client.get(url).context['cl'].result_list, [own_leave, other_leave])
        self.grant(self.staff.user_permissions, 'hrm', 'add_leaverequest')
        response = self.client.post(reverse('admin:hrm_leaverequest_add'), {
            'employee': employee.pk, 'leave_type': leave_type.pk, 'start_date': today.isoformat(),
            'end_date': today.isoformat(), 'status': LeaveRequest.Status.CANCELLED,
        })
        self.assertIn('You cannot review your own leave request.', response.context['adminform'].form.non_field_errors())

    def test_loan_installment_uses_standard_direct_and_group_permissions(self):
        employee = Employee.objects.create(employee_code='TEST-LOAN', full_name='Borrower')
        month = timezone.localdate().replace(day=1)
        loan = EmployeeLoan.objects.create(employee=employee, currency_id='BDT', reference='TEST-LOAN',
                                           principal=200, disbursed_on=month)
        add = reverse('admin:hrm_loaninstallment_add')
        payload = {'loan': loan.pk, 'month': month.strftime('%Y-%m'), 'amount': '50'}
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(add).status_code, 403)
        self.grant(self.staff.user_permissions, 'hrm', 'add_loaninstallment')
        self.assertEqual(self.client.post(add, payload).status_code, 302)
        installment = loan.installments.get()
        self.assertEqual(installment.create_by, self.staff.username)
        detail = reverse('admin:hrm_loaninstallment_change', args=[installment.pk])
        self.assertEqual(self.client.post(detail, {**payload, 'amount': '80'}).status_code, 403)

        role = Group.objects.create(name='Payroll editors')
        self.staff.groups.add(role)
        self.grant(role.permissions, 'hrm', 'change_loaninstallment', 'delete_loaninstallment')
        self.assertEqual(self.client.post(detail, {**payload, 'amount': '80'}).status_code, 302)
        installment.refresh_from_db()
        self.assertEqual(installment.amount, Decimal('80'))
        delete = reverse('admin:hrm_loaninstallment_delete', args=[installment.pk])
        self.assertEqual(self.client.post(delete, {'post': 'yes'}).status_code, 302)
        self.assertFalse(loan.installments.exists())

    def test_admin_can_create_payroll_rows_with_system_managed_fields(self):
        period = PayrollPeriod.objects.create(month=timezone.localdate().replace(day=1))
        run = PayrollRun.objects.create(period=period, currency_id='BDT', calculated_by=self.root)
        employee = Employee.objects.create(employee_code='PAY-ADMIN', full_name='Payroll record')
        self.client.force_login(self.root)
        response = self.client.post(reverse('admin:hrm_payslip_add'), {
            'payroll_run': run.pk, 'employee': employee.pk, 'gross_pay': '100', 'total_deductions': '0', 'net_pay': '100',
        })
        self.assertEqual(response.status_code, 302)
        slip = Payslip.objects.get(payroll_run=run)
        self.assertEqual((slip.employee_code, slip.employee_name), (employee.employee_code, employee.full_name))
        response = self.client.post(reverse('admin:hrm_payslipline_add'), {
            'payslip': slip.pk, 'line_code': 'BASIC', 'line_name': 'Basic pay', 'line_type': 'earning', 'amount': '100',
        })
        self.assertEqual(response.status_code, 302)
        line = PayslipLine.objects.get(payslip=slip)
        self.assertEqual((line.code, line.name, line.type), ('BASIC', 'Basic pay', 'earning'))
        payment_add = reverse('admin:hrm_salarypayment_add')
        payment = {'payslip': slip.pk, 'amount': '50', 'method': 'cash'}
        response = self.client.post(payment_add, {**payment, 'paid_on': period.month.isoformat()})
        self.assertTrue(response.context['adminform'].form.non_field_errors())
        self.assertEqual(self.client.post(payment_add, payment).status_code, 302)
        self.assertIsNotNone(SalaryPayment.objects.get(payslip=slip).request_key)

    def test_sales_return_admin_permissions_allow_writes_within_warehouse_scope(self):
        main = WareHouse.objects.create(name='Main', address='Main address', phone='1')
        branch = WareHouse.objects.create(name='Branch', address='Branch address', phone='2')
        order = SalesOrder.objects.create(warehouse=main, currency_id='BDT', exchange_rate=1, is_completed=True)
        remote_order = SalesOrder.objects.create(warehouse=branch, currency_id='BDT', exchange_rate=1, is_completed=True)
        record = SalesReturn.objects.create(sales_order=order, notes='Original notes')
        remote_record = SalesReturn.objects.create(sales_order=remote_order)
        changelist = reverse('admin:sale_salesreturn_changelist')
        detail = reverse('admin:sale_salesreturn_change', args=[record.pk])
        self.client.force_login(self.staff)
        self.grant(self.staff.user_permissions, 'sale', 'view_salesreturn')
        self.assertEqual(self.client.get(changelist).status_code, 403)

        assign_warehouse_role(self.staff, warehouse=main)
        self.assertEqual(list(self.client.get(changelist).context['cl'].result_list), [record])
        response = self.client.get(detail)
        self.assertContains(response, record.return_number)
        self.assertContains(response, 'Original notes')
        self.assertNotContains(response, 'name="_save"')
        remote_detail = reverse('admin:sale_salesreturn_change', args=[remote_record.pk])
        self.assertRedirects(self.client.get(remote_detail), reverse('admin:index'))

        self.grant(self.staff.user_permissions, 'sale', 'add_salesreturn', 'change_salesreturn', 'delete_salesreturn')
        payload = {'sales_order': order.pk, 'return_date': record.return_date.isoformat(), 'notes': 'Changed'}
        response = self.client.post(detail, {**payload, 'sales_order': remote_order.pk})
        self.assertIn('sales_order', response.context['adminform'].form.errors)
        self.assertEqual(self.client.post(detail, payload).status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.notes, 'Changed')
        add = reverse('admin:sale_salesreturn_add')
        self.assertEqual(self.client.post(add, payload).status_code, 302)
        delete = reverse('admin:sale_salesreturn_delete', args=[record.pk])
        self.assertEqual(self.client.post(delete, {'post': 'yes'}).status_code, 302)
        self.assertFalse(SalesReturn.objects.filter(pk=record.pk).exists())

        completed = SalesReturn.objects.create(sales_order=order, is_completed=True, completed_at=timezone.now())
        completed_detail = reverse('admin:sale_salesreturn_change', args=[completed.pk])
        self.assertEqual(self.client.post(completed_detail, payload).status_code, 403)

    def test_role_warehouse_assignment_requires_crud_and_management_permission(self):
        warehouse = WareHouse.objects.create(name='Main', address='Main address', phone='1')
        own_assignment = assign_warehouse_role(self.staff, warehouse=warehouse)
        target = Group.objects.create(name='Stock operators')
        add = reverse('admin:warehouse_rolewarehouseaccess_add')
        self.client.force_login(self.staff)
        self.grant(self.staff.user_permissions, 'warehouse', 'add_rolewarehouseaccess', 'delete_rolewarehouseaccess')
        self.assertEqual(self.client.get(add).status_code, 403)
        self.grant(own_assignment.role.permissions, 'warehouse', 'manage_warehouse_access')
        self.assertEqual(self.client.post(add, {'role': target.pk, 'warehouse': warehouse.pk}).status_code, 302)
        assignment = RoleWarehouseAccess.objects.get(role=target)
        self.assertEqual(assignment.warehouse, warehouse)
        own_delete = reverse('admin:warehouse_rolewarehouseaccess_delete', args=[own_assignment.pk])
        self.assertEqual(self.client.post(own_delete, {'post': 'yes'}).status_code, 403)
        self.assertTrue(RoleWarehouseAccess.objects.filter(pk=own_assignment.pk).exists())
        delete = reverse('admin:warehouse_rolewarehouseaccess_delete', args=[assignment.pk])
        self.assertEqual(self.client.post(delete, {'post': 'yes'}).status_code, 302)
        self.assertFalse(RoleWarehouseAccess.objects.filter(pk=assignment.pk).exists())
