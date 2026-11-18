from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from hrm.models import Employee, LeaveRequest, LeaveType


class LeaveRequestTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.employee = Employee.objects.create(employee_code='LEAVE-1', full_name='Employee')
        cls.other_employee = Employee.objects.create(employee_code='LEAVE-2', full_name='Other Employee')
        cls.leave_type = LeaveType.objects.create(code='annual', name='Annual leave', is_paid=True)
        cls.reviewer = get_user_model().objects.create_user(username='leave-reviewer')

    def leave(self, start=10, end=12, **changes):
        return LeaveRequest(**{
            'employee': self.employee, 'leave_type': self.leave_type,
            'start_date': date(2026, 11, start), 'end_date': date(2026, 11, end),
            'create_by': 'tester', 'modified_by': 'tester', **changes,
        })

    def test_single_day_request_is_pending_and_reversed_dates_are_invalid(self):
        request = self.leave(end=10)
        request.full_clean()
        request.save()
        self.assertEqual(request.status, LeaveRequest.Status.PENDING)
        reversed_dates = self.leave(end=9)
        with self.assertRaises(ValidationError) as error:
            reversed_dates.full_clean()
        self.assertIn('end_date', error.exception.message_dict)
        with self.assertRaises(IntegrityError), transaction.atomic():
            reversed_dates.save()

    def test_approved_overlap_uses_inclusive_dates_and_only_the_same_employee(self):
        approval = {'status': LeaveRequest.Status.APPROVED, 'reviewed_by': self.reviewer,
                    'reviewed_at': timezone.now()}
        existing = self.leave(**approval)
        existing.full_clean()
        existing.save()
        existing.full_clean()

        for start, end in [(8, 10), (11, 11), (12, 14), (9, 13)]:
            with self.subTest(start=start, end=end), self.assertRaises(ValidationError) as error:
                self.leave(start, end, **approval).full_clean()
            self.assertIn('start_date', error.exception.message_dict)

        self.leave(13, 14, **approval).full_clean()
        self.leave(employee=self.other_employee, **approval).full_clean()
        self.leave().full_clean()
        self.leave(status=LeaveRequest.Status.CANCELLED).full_clean()

    def test_approval_and_rejection_require_reviewer_and_review_time(self):
        for status in (LeaveRequest.Status.APPROVED, LeaveRequest.Status.REJECTED):
            invalid = self.leave(status=status)
            with self.subTest(status=status), self.assertRaises(ValidationError) as error:
                invalid.full_clean()
            self.assertIn('reviewed_by', error.exception.message_dict)
            self.assertIn('reviewed_at', error.exception.message_dict)
            with self.assertRaises(IntegrityError), transaction.atomic():
                invalid.save()

        review = {'reviewed_by': self.reviewer, 'reviewed_at': timezone.now()}
        self.leave(status=LeaveRequest.Status.CANCELLED, **review).full_clean()
        with self.assertRaises(ValidationError) as error:
            self.leave(**review).full_clean()
        self.assertIn('status', error.exception.message_dict)
