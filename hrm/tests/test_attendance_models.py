from datetime import date, datetime, time, timedelta, timezone as datetime_timezone

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.utils import timezone

from hrm.models import Attendance, Employee, EmployeeAttendanceStatus, EmployeeScheduleAssignment, Holiday, WorkSchedule
from warehouse.models import WareHouse


@override_settings(TIME_ZONE='Asia/Dhaka')
class AttendanceModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.employee = Employee.objects.create(employee_code='ATT-001', full_name='Attendance Employee')
        cls.schedule = WorkSchedule.objects.create(code='office', name='Office', working_days=[6, 0, 1, 2, 3],
            start_time=time(9), end_time=time(18), break_minutes=60, grace_minutes=10)
        cls.work_date = date(2026, 11, 18)

    def clean(self, instance):
        instance.full_clean(exclude=['create_by', 'modified_by'])
        return instance

    def stamp(self, hour, day=18):
        return timezone.make_aware(datetime(2026, 11, day, hour), timezone.get_default_timezone())

    def assignment(self, **kwargs):
        return EmployeeScheduleAssignment(employee=self.employee, schedule=self.schedule,
            **{'effective_from': date(2026, 11, 1), **kwargs})

    def test_schedule_normalizes_identity_and_supports_overnight_shifts(self):
        self.schedule.code = ' NIGHT '
        self.schedule.name = ' Night '
        self.schedule.start_time, self.schedule.end_time = time(22), time(6)
        self.clean(self.schedule)
        self.assertEqual((self.schedule.code, self.schedule.name), ('night', 'Night'))

    def test_working_days_reject_invalid_types_duplicates_and_out_of_range_values(self):
        for value in [[], {}, 'Monday', [True], [0, 0], [-1], [7], [1.5], [[0]]]:
            with self.subTest(value=value), self.assertRaises(ValidationError) as error:
                self.schedule.working_days = value
                self.clean(self.schedule)
            self.assertIn('working_days', error.exception.message_dict)

    def test_schedule_rejects_zero_duration_excessive_breaks_and_negative_minutes(self):
        for changes in [{'end_time': time(9)}, {'break_minutes': 540}, {'break_minutes': -1}, {'grace_minutes': -1}]:
            schedule = WorkSchedule(code='test', name='Test', working_days=[0], start_time=time(9), end_time=time(18))
            for key, value in changes.items():
                setattr(schedule, key, value)
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.clean(schedule)
        with self.assertRaises(IntegrityError), transaction.atomic():
            WorkSchedule.objects.filter(pk=self.schedule.pk).update(end_time=time(9))

    def test_assignment_periods_are_inclusive_and_may_be_open_ended(self):
        first = self.assignment(effective_to=date(2026, 11, 10))
        self.clean(first).save()
        self.clean(first)  # Updating itself must not count as an overlap.
        for start, end in [(date(2026, 10, 1), None), (date(2026, 11, 10), date(2026, 11, 20)),
                           (date(2026, 11, 2), date(2026, 11, 3))]:
            with self.subTest(start=start, end=end), self.assertRaises(ValidationError):
                self.clean(self.assignment(effective_from=start, effective_to=end))
        ongoing = self.assignment(effective_from=date(2026, 11, 11))
        self.clean(ongoing).save()
        with self.assertRaises(ValidationError):
            self.clean(self.assignment(effective_from=date(2027, 1, 1)))
        other = Employee.objects.create(employee_code='ATT-002', full_name='Other Employee')
        self.clean(EmployeeScheduleAssignment(employee=other, schedule=self.schedule, effective_from=self.work_date)).save()

    def test_assignment_rejects_reversed_dates_in_validation_and_database(self):
        assignment = self.assignment(effective_to=date(2026, 10, 31))
        with self.assertRaises(ValidationError):
            self.clean(assignment)
        with self.assertRaises(IntegrityError), transaction.atomic():
            assignment.save()

    def test_holidays_are_unique_per_date_and_scope_including_company_wide(self):
        warehouse = WareHouse.objects.create(name='Office', code='ATT', address='Dhaka', phone='123')
        for scope in [None, warehouse]:
            holiday = Holiday(name=' Holiday ', date=self.work_date, warehouse=scope)
            self.clean(holiday).save()
            self.assertEqual(holiday.name, 'Holiday')
            duplicate = Holiday(name='Duplicate', date=self.work_date, warehouse=scope)
            with self.subTest(scope=scope), self.assertRaises(ValidationError):
                self.clean(duplicate)
            with self.assertRaises(IntegrityError), transaction.atomic():
                duplicate.save()
        with self.assertRaises(ValidationError):
            self.clean(Holiday(name='   ', date=self.work_date + timedelta(days=1)))
        with self.assertRaises(ProtectedError):
            warehouse.delete()

    def test_attendance_defaults_to_unrecorded_and_is_unique_per_employee_date(self):
        attendance = Attendance(employee=self.employee, work_date=self.work_date)
        self.clean(attendance).save()
        self.assertEqual(attendance.status_id, EmployeeAttendanceStatus.Code.UNRECORDED)
        duplicate = Attendance(employee=self.employee, work_date=self.work_date)
        with self.assertRaises(ValidationError):
            self.clean(duplicate)
        with self.assertRaises(IntegrityError), transaction.atomic():
            duplicate.save()

    def test_attendance_supports_open_checkins_and_overnight_checkout(self):
        attendance = Attendance(employee=self.employee, work_date=self.work_date,
            check_in=self.stamp(22), status_id=EmployeeAttendanceStatus.Code.PRESENT)
        self.clean(attendance).save()
        attendance.check_out = self.stamp(6, day=19)
        self.clean(attendance).save()
        attendance.refresh_from_db()
        self.assertEqual(attendance.check_out - attendance.check_in, timedelta(hours=8))

    def test_checkin_uses_local_work_date_rather_than_utc_date(self):
        attendance = Attendance(employee=self.employee, work_date=self.work_date,
            check_in=datetime(2026, 11, 17, 20, tzinfo=datetime_timezone.utc))
        self.clean(attendance)
        attendance.work_date = date(2026, 11, 17)
        with self.assertRaises(ValidationError) as error:
            self.clean(attendance)
        self.assertIn('check_in', error.exception.message_dict)

    def test_attendance_rejects_checkout_without_checkin_and_reversed_times(self):
        for check_in in [None, self.stamp(10)]:
            attendance = Attendance(employee=self.employee, work_date=self.work_date,
                check_in=check_in, check_out=self.stamp(9))
            with self.subTest(check_in=check_in), self.assertRaises(ValidationError):
                self.clean(attendance)
            with self.assertRaises(IntegrityError), transaction.atomic():
                attendance.save()

    def test_attendance_status_matches_recorded_times(self):
        codes = EmployeeAttendanceStatus.Code
        with self.assertRaises(ValidationError):
            self.clean(Attendance(employee=self.employee, work_date=self.work_date, status_id=codes.PRESENT))
        for status in [codes.ABSENT, codes.ON_LEAVE, codes.HOLIDAY, codes.DAY_OFF]:
            self.clean(Attendance(employee=self.employee, work_date=self.work_date, status_id=status))
            with self.subTest(status=status), self.assertRaises(ValidationError):
                self.clean(Attendance(employee=self.employee, work_date=self.work_date, status_id=status, check_in=self.stamp(9)))

    def test_archiving_employee_preserves_attendance_and_schedule_history(self):
        assignment = self.clean(self.assignment())
        assignment.save()
        attendance = self.clean(Attendance(employee=self.employee, work_date=self.work_date))
        attendance.save()
        self.employee.delete(actor='hr-manager')
        attendance.refresh_from_db()
        assignment.refresh_from_db()
        self.clean(attendance)
        self.clean(assignment)
        self.assertTrue(attendance.employee.is_archived)
        self.assertTrue(assignment.employee.is_archived)
        with self.assertRaises(ProtectedError):
            self.schedule.delete()
