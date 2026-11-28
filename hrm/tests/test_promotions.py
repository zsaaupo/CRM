from datetime import date, datetime, timezone as utc_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from audit_log.models import AuditLog
from hrm.models import (Designation, Employee, EmployeeCompensation, EmployeeDesignationChange, SalaryComponent,
                        SalaryStructure, SalaryStructureLine)
from hrm.payroll import preview_payroll, save_payroll
from hrm.payroll_reports import payslip_document
from hrm.payroll_workflow import transition_run
from hrm.promotions import create_opening_designation


@override_settings(TIME_ZONE='Asia/Dhaka')
class PromotionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.hr = users.create_user(username='promotion-hr', is_staff=True)
        cls.viewer = users.create_user(username='promotion-viewer')
        cls.junior = Designation.objects.create(code='promo-junior', name='Junior')
        cls.senior = Designation.objects.create(code='promo-senior', name='Senior')
        cls.lead = Designation.objects.create(code='promo-lead', name='Lead')
        cls.inactive = Designation.objects.create(code='promo-inactive', name='Inactive', is_active=False)
        cls.employee = Employee.objects.create(employee_code='PROMO-01', full_name='Employee One',
                                               joining_date=date(2026, 1, 1), designation=cls.junior)
        create_opening_designation(cls.employee, cls.hr)
        cls.grant(cls.hr, 'view_employeedesignationchange', 'add_employeedesignationchange',
                  'correct_employeedesignationchange', 'view_employee', 'change_employee', 'add_employee')
        cls.grant(cls.viewer, 'view_employeedesignationchange')

    @staticmethod
    def grant(user, *codes):
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='hrm', codename__in=codes))

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.log_in(self.hr)
        self.api = reverse('hrm:promotion-list')
        self.employee_url = reverse('hrm:employee-detail', args=[self.employee.pk])

    def log_in(self, user):
        self.client.force_login(user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)

    def book(self, designation, day, **extra):
        return self.client.post(self.api, {'employee_id': self.employee.pk, 'designation_id': designation.pk,
                                          'effective_date': day, **extra}, format='json')

    def details(self, entry_id):
        return self.client.get(reverse('hrm:promotion-detail', args=[entry_id])).data

    def test_records_and_audits_backdated_changes_with_the_original_predecessor(self):
        with patch('hrm.promotions.timezone.localdate', return_value=date(2026, 5, 1)):
            later = self.book(self.lead, '2026-04-01', recorded_by_id=self.viewer.pk)
            self.assertEqual(later.status_code, 201, later.data)
            earlier = self.book(self.senior, '2026-03-01')
            self.assertEqual(earlier.status_code, 201, earlier.data)
            self.assertEqual(self.book(self.senior, '2026-04-01').status_code, 409)
            self.assertEqual(self.book(self.junior, '2026-02-01').status_code, 400)
            self.assertEqual(self.book(self.senior, '2025-12-31').status_code, 400)
            self.assertEqual(self.book(self.inactive, '2026-05-01').status_code, 400)
        later_id = later.data['designation_change']['id']
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.designation, self.lead)
        row = self.details(later_id)
        self.assertEqual(row['recorded_by_id'], self.hr.pk)
        self.assertEqual(row['previous_designation_name'], 'Senior')
        self.assertEqual(row['recorded_previous_designation_name'], 'Junior')
        self.assertEqual(row['designation_name'], 'Lead')
        event = AuditLog.objects.get(resource='hrm.employeedesignationchange', action='recorded', object_id=later_id)
        self.assertEqual(event.actor, self.hr.username)
        self.assertEqual(event.subject_id, self.employee.pk)
        self.assertIn('Junior → Lead', event.reference)
        with self.assertRaisesMessage(ValidationError, 'append-only'):
            EmployeeDesignationChange.objects.filter(pk=later_id).update(remarks='rewrite')

    def test_employee_reads_and_edits_work_after_a_missed_scheduler_run(self):
        with patch('hrm.promotions.timezone.localdate', return_value=date(2026, 2, 1)):
            pending = self.book(self.senior, '2026-03-01')
            self.assertEqual(pending.status_code, 201, pending.data)
            self.assertEqual(pending.data['designation_change']['status'], 'Scheduled')
            self.assertEqual(self.client.get(self.employee_url).data['designation'], self.junior.pk)
        with patch('hrm.promotions.timezone.localdate', return_value=date(2026, 3, 10)):
            self.assertEqual(self.client.get(self.employee_url).data['designation'], self.senior.pk)
            employees = self.client.get(reverse('hrm:employee-list-view'), {'designation': self.senior.pk, 'q': 'Senior'})
            self.assertEqual(employees.data['count'], 1)
            self.assertEqual(self.client.patch(self.employee_url, {'designation': self.lead.pk}, format='json').status_code, 400)
            update = self.client.patch(self.employee_url, {'designation': self.senior.pk, 'workplace': 'Head office'}, format='json')
            self.assertEqual(update.status_code, 200, update.data)
            self.employee.refresh_from_db()
            self.assertEqual(self.employee.designation, self.junior)
            call_command('sync_employee_designations', verbosity=0)
            call_command('sync_employee_designations', verbosity=0)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.designation, self.senior)
        self.assertEqual(AuditLog.objects.filter(resource='hrm.employeedesignationchange', action='applied').count(), 1)

    def test_corrections_cancellations_and_departures_preserve_history(self):
        with patch('hrm.promotions.timezone.localdate', return_value=date(2026, 2, 1)):
            original = self.book(self.senior, '2026-03-01').data['designation_change']
            edit = reverse('hrm:promotion-correction', args=[original['id']])
            self.assertEqual(self.client.post(edit, {'designation_id': None, 'remarks': ''}, format='json').status_code, 400)
            correction = self.client.post(edit, {'designation_id': self.lead.pk, 'remarks': 'Approved title'}, format='json')
            self.assertEqual(correction.status_code, 201, correction.data)
            correction_id = correction.data['designation_change']['id']
            self.assertEqual(self.details(original['id'])['status'], 'Superseded')
            self.assertEqual(self.details(original['id'])['designation_name'], 'Senior')
            self.assertEqual(self.client.post(edit, {'designation_id': None, 'remarks': 'Duplicate'}, format='json').status_code, 409)
            scheduled = self.book(self.senior, '2026-04-01').data['designation_change']
        with patch('hrm.promotions.timezone.localdate', return_value=date(2026, 3, 10)):
            self.assertEqual(self.client.get(self.employee_url).data['designation'], self.lead.pk)
            cancel_url = reverse('hrm:promotion-correction', args=[correction_id])
            cancelled = self.client.post(cancel_url, {'designation_id': None, 'remarks': 'Change withdrawn'}, format='json')
            self.assertEqual(cancelled.status_code, 201, cancelled.data)
            self.assertEqual(cancelled.data['designation_change']['status'], 'Cancelled')
            self.assertEqual(self.client.get(self.employee_url).data['designation'], self.junior.pk)
        with patch('hrm.managers.timezone.now', return_value=datetime(2026, 3, 15, tzinfo=utc_timezone.utc)):
            self.employee.delete(actor=self.hr.username)
        self.assertEqual(self.details(scheduled['id'])['status'], 'Skipped outside employment')
        designation_changes = self.client.get(self.api, {'employee_id': self.employee.pk}).data
        self.assertEqual(designation_changes['count'], 5)
        self.assertEqual(Employee.all_objects.get(pk=self.employee.pk).designation, self.junior)

    def test_pages_and_options_use_separate_read_write_and_correction_permissions(self):
        self.assertContains(self.client.get(reverse('hrm_pages:promotion-list')), 'Record change')
        self.assertContains(self.client.get(reverse('hrm_pages:add-promotion')), 'Record designation change')
        self.assertContains(self.client.get(reverse('hrm_pages:employee-detail', args=[self.employee.pk])),
                            f'/hrm/promotions/?employee_id={self.employee.pk}')
        self.assertContains(self.client.get(reverse('admin:hrm_employee_change', args=[self.employee.pk])),
                            f'/hrm/promotions/?employee_id={self.employee.pk}')
        Employee.objects.create(employee_code='PROMO-LEFT', full_name='Former employee', status_id='left')
        options = self.client.get(reverse('hrm:promotion-options')).data
        self.assertEqual([entry['value'] for entry in options['employee_id']], [self.employee.pk])
        self.assertNotIn(self.inactive.pk, [entry['value'] for entry in options['designation_id']])
        entry_id = self.book(self.senior, '2026-03-01').data['designation_change']['id']
        self.assertContains(self.client.get(reverse('hrm_pages:correct-promotion', args=[entry_id])), 'Record correction')
        self.log_in(self.viewer)
        self.assertEqual(self.client.get(self.api).status_code, 200)
        self.assertEqual(self.client.get(reverse('hrm:promotion-options')).status_code, 403)
        self.assertEqual(self.book(self.lead, '2026-04-01').status_code, 403)
        self.assertNotContains(self.client.get(reverse('hrm_pages:promotion-list')), 'Record change')
        self.assertNotContains(self.client.get(reverse('hrm_pages:promotion-detail', args=[entry_id])), 'Record correction')
        self.assertEqual(self.client.post(reverse('hrm:promotion-correction', args=[entry_id]),
                                         {'designation_id': None, 'remarks': 'Cancel'}, format='json').status_code, 403)

    def test_payroll_snapshots_the_last_payable_designation(self):
        with patch('hrm.promotions.timezone.localdate', return_value=date(2026, 4, 1)):
            self.assertEqual(self.book(self.senior, '2026-02-15').status_code, 201)
            self.assertEqual(self.book(self.lead, '2026-03-01').status_code, 201)
        structure = SalaryStructure.objects.create(code='promo-pay', name='Monthly', currency_id='BDT')
        basic = SalaryComponent.objects.create(code='promo-base', name='Basic salary', type='earning')
        SalaryStructureLine.objects.create(salary_structure=structure, component=basic, calculation_method='basic_salary')
        month = date(2026, 2, 1)
        EmployeeCompensation.objects.create(employee=self.employee, salary_structure=structure, basic_salary=30000,
                                            effective_from=month)
        preview = preview_payroll(month, 'BDT')
        self.assertTrue(preview['can_calculate'], preview)
        self.assertEqual(preview['payslips'][0]['designation_name'], 'Senior')
        run, _ = save_payroll(month, 'BDT', preview['preview_token'], self.hr)
        transition_run(run.pk, 'review', self.hr)
        transition_run(run.pk, 'approve', self.hr)
        self.senior.name = 'Renamed senior'
        self.senior.save(update_fields=['name'])
        slip = run.payslips.get(employee=self.employee)
        self.assertEqual(slip.designation_name, 'Senior')
        self.assertEqual(payslip_document(slip.pk)['designation'], 'Senior')
