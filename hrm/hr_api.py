"""HR-01, HR-02, HR-03 API views & serializers."""

from datetime import date
from uuid import UUID

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.serializers import AUDIT_FIELDS, CatalogSerializer
from common.view import BaseAPIMixin, BaseDetailAPIView, BaseListCreateAPIView
from .leave_policies import allocate_annual_leave, expire_carry_forward
from .models import (
    AttendanceImportBatch, AttendanceImportRow, Employee, EmployeePromotion, LatePolicy,
    LeaveBalance, LeavePolicy, ManualPayrollVoucher, Notice, PayrollMonthPolicy, SalaryAdvance,
    SalaryIncrement, SalaryPayment, WorkingCalendar,
)
from .workforce import (
    apply_due_promotions, approve_advance, build_calendar_month, commit_attendance_import,
    create_manual_voucher, disburse_advance, mark_advance_paid_external, preview_attendance_import,
    reconcile_voucher_to_payslip, record_promotion, reject_advance, request_advance, visible_notices_for_user,
)


class EmployeePromotionSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)

    class Meta:
        model = EmployeePromotion
        fields = [
            'id', 'employee', 'employee_name', 'previous_designation', 'new_designation',
            'previous_department', 'new_department', 'effective_date', 'salary_increment_amount',
            'reason', 'notes', 'created_by', 'applied_at', 'created_at',
        ]
        read_only_fields = ['id', 'previous_designation', 'previous_department', 'created_by', 'applied_at', 'created_at']


class PromotionListCreateAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'head', 'options']

    def get(self, request):
        if not (request.user.is_superuser or request.user.has_perm('hrm.view_employeepromotion') or request.user.has_perm('hrm.approve_promotion')):
            emp = Employee.objects.filter(user_id=request.user.pk).first()
            qs = EmployeePromotion.objects.filter(employee=emp) if emp else EmployeePromotion.objects.none()
        else:
            qs = EmployeePromotion.objects.all()
        if request.query_params.get('employee'):
            qs = qs.filter(employee_id=int(request.query_params['employee']))
        return Response({'results': EmployeePromotionSerializer(qs.select_related('employee')[:100], many=True).data})

    def post(self, request):
        promo = record_promotion(
            request.user,
            employee_id=int(request.data.get('employee')),
            effective_date=date.fromisoformat(str(request.data.get('effective_date'))),
            new_designation_id=request.data.get('new_designation') or None,
            new_department_id=request.data.get('new_department') or None,
            salary_increment_amount=request.data.get('salary_increment_amount') or 0,
            reason=request.data.get('reason') or '',
            notes=request.data.get('notes') or '',
        )
        return Response(EmployeePromotionSerializer(promo).data, status=status.HTTP_201_CREATED)


class PromotionApplyDueAPIView(BaseAPIMixin, APIView):
    required_permissions = ('hrm.approve_promotion',)
    http_method_names = ['post', 'options']

    def post(self, request):
        as_of = date.fromisoformat(request.data['as_of']) if request.data.get('as_of') else None
        count = apply_due_promotions(as_of_date=as_of, actor=request.user)
        return Response({'applied': count})


class NoticeSerializer(CatalogSerializer):
    class Meta:
        model = Notice
        fields = [
            'id', 'title', 'body', 'target_department', 'target_warehouse',
            'publish_date', 'expiry_date', 'is_active', 'created_by', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = [*AUDIT_FIELDS, 'created_by']


class NoticeListCreateAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'head', 'options']

    def get(self, request):
        qs = visible_notices_for_user(request.user)
        return Response({'results': NoticeSerializer(qs[:100], many=True).data})

    def post(self, request):
        if not (request.user.is_superuser or request.user.has_perm('hrm.publish_notice')):
            return Response({'detail': 'Permission hrm.publish_notice required.'}, status=status.HTTP_403_FORBIDDEN)
        ser = NoticeSerializer(data=request.data, context={'request': request})
        ser.is_valid(raise_exception=True)
        ser.save(created_by=request.user, create_by=request.user.get_username(), modified_by=request.user.get_username())
        return Response(ser.data, status=status.HTTP_201_CREATED)


class NoticeDetailAPIView(BaseDetailAPIView):
    serializer_class = NoticeSerializer
    queryset = Notice.objects.all()

    def get_queryset(self):
        return visible_notices_for_user(self.request.user)


class AttendanceImportRowSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceImportRow
        fields = ['id', 'device_employee_id', 'raw_timestamp', 'parsed_ts', 'punch_kind', 'employee', 'work_date', 'status', 'notes']


class AttendanceImportPreviewAPIView(BaseAPIMixin, APIView):
    required_permissions = ('hrm.import_attendance',)
    http_method_names = ['post', 'options']

    def post(self, request):
        batch = preview_attendance_import(
            request.user,
            rows=request.data.get('rows'),
            csv_text=request.data.get('csv_text') or '',
            date_format=request.data.get('date_format') or '%Y-%m-%d %H:%M:%S',
            tz_name=request.data.get('timezone') or '',
        )
        rows = AttendanceImportRowSerializer(batch.rows.all(), many=True).data
        return Response({'batch_id': batch.pk, 'status': batch.status, 'rows': rows}, status=status.HTTP_201_CREATED)


class AttendanceImportCommitAPIView(BaseAPIMixin, APIView):
    required_permissions = ('hrm.import_attendance',)
    http_method_names = ['post', 'options']

    def post(self, request):
        batch_id = int(request.data.get('batch_id'))
        res = commit_attendance_import(
            request.user,
            batch_id,
            reviewed_dates=request.data.get('reviewed_dates'),
            resolve_conflicts=bool(request.data.get('resolve_conflicts', False)),
        )
        return Response(res)


class WorkingCalendarSerializer(CatalogSerializer):
    class Meta:
        model = WorkingCalendar
        fields = ['id', 'effective_from', 'weekly_off_days', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS


class WorkingCalendarListCreateAPIView(BaseListCreateAPIView):
    serializer_class = WorkingCalendarSerializer
    queryset = WorkingCalendar.objects.all()
    response_key = 'working_calendar'


class CalendarMonthAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        month_raw = request.query_params.get('month')
        if month_raw:
            parts = month_raw.split('-')
            month_first = date(int(parts[0]), int(parts[1]), 1)
        else:
            month_first = timezone.localdate().replace(day=1)
        emp = None
        if request.query_params.get('employee'):
            emp = get_object_or_404(Employee, pk=int(request.query_params['employee']))
        return Response(build_calendar_month(month_first, employee=emp))


# ---------------- HR-02 ----------------


class LeavePolicySerializer(CatalogSerializer):
    class Meta:
        model = LeavePolicy
        fields = ['id', 'leave_type', 'effective_from', 'annual_quota', 'carry_forward_limit', 'carry_expiry_month_day', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS


class LeavePolicyListCreateAPIView(BaseListCreateAPIView):
    serializer_class = LeavePolicySerializer
    queryset = LeavePolicy.objects.select_related('leave_type')
    response_key = 'leave_policy'


class LeaveBalanceSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    leave_type_name = serializers.CharField(source='leave_type.name', read_only=True)
    remaining_carry = serializers.DecimalField(max_digits=8, decimal_places=2, read_only=True)
    remaining_current = serializers.DecimalField(max_digits=8, decimal_places=2, read_only=True)

    class Meta:
        model = LeaveBalance
        fields = [
            'id', 'employee', 'employee_name', 'leave_type', 'leave_type_name', 'year',
            'allocated', 'carried', 'carry_used', 'used', 'expired', 'prior_year_known',
            'remaining_carry', 'remaining_current',
        ]


class LeaveBalanceListAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        qs = LeaveBalance.objects.select_related('employee', 'leave_type')
        if not (request.user.is_superuser or request.user.has_perm('hrm.view_leavebalance') or request.user.has_perm('hrm.allocate_leave')):
            qs = qs.filter(employee__user_id=request.user.pk)
        if request.query_params.get('year'):
            qs = qs.filter(year=int(request.query_params['year']))
        if request.query_params.get('employee'):
            qs = qs.filter(employee_id=int(request.query_params['employee']))
        return Response({'results': LeaveBalanceSerializer(qs[:200], many=True).data})


class LeaveBalanceAllocateAPIView(BaseAPIMixin, APIView):
    required_permissions = ('hrm.allocate_leave',)
    http_method_names = ['post', 'options']

    def post(self, request):
        year = int(request.data.get('year') or timezone.localdate().year)
        emp_ids = request.data.get('employee_ids')
        req_key = request.data.get('request_key')
        res, created = allocate_annual_leave(
            year, employee_ids=emp_ids, request_key=UUID(str(req_key)) if req_key else None, actor=request.user,
        )
        return Response({**res, 'idempotent_replay': not created})


class LeaveBalanceExpireCarryAPIView(BaseAPIMixin, APIView):
    required_permissions = ('hrm.allocate_leave',)
    http_method_names = ['post', 'options']

    def post(self, request):
        year = int(request.data.get('year') or timezone.localdate().year)
        as_of = date.fromisoformat(request.data['as_of']) if request.data.get('as_of') else None
        count = expire_carry_forward(year, as_of_date=as_of, actor=request.user)
        return Response({'expired_balances': count})


class LatePolicySerializer(CatalogSerializer):
    class Meta:
        model = LatePolicy
        fields = [
            'id', 'default_check_in', 'default_check_out', 'grace_minutes',
            'monthly_late_allowance', 'late_marks_per_leave_day', 'early_leave_threshold_minutes',
            'effective_from', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = AUDIT_FIELDS


class LatePolicyListCreateAPIView(BaseListCreateAPIView):
    serializer_class = LatePolicySerializer
    queryset = LatePolicy.objects.all()
    response_key = 'late_policy'


class PayrollMonthPolicyAPIView(BaseAPIMixin, APIView):
    required_permissions = ('hrm.view_payrollrun',)
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        m_raw = request.query_params.get('month')
        if not m_raw:
            raise ValidationError({'month': 'Specify month=YYYY-MM or YYYY-MM-01.'})
        parts = m_raw.split('-')
        m_date = date(int(parts[0]), int(parts[1]), 1)
        pol = PayrollMonthPolicy.objects.filter(month=m_date).first()
        if pol is None:
            return Response({'month': m_date.isoformat(), 'frozen': False, 'snapshot': None})
        return Response({
            'month': pol.month.isoformat(),
            'frozen': True,
            'calc_version': pol.calc_version,
            'frozen_at': pol.frozen_at,
            'snapshot': pol.snapshot,
        })


# ---------------- HR-03 ----------------


class SalaryAdvanceSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)

    class Meta:
        model = SalaryAdvance
        fields = [
            'id', 'employee', 'employee_name', 'currency', 'requested_amount', 'approved_amount',
            'status', 'installment_count', 'repayment_start_month', 'reason',
            'approved_by', 'approved_at', 'disbursed_by', 'disbursed_at',
            'account', 'loan', 'external_paid_marker', 'external_reference', 'created_at',
        ]


class SalaryAdvanceListCreateAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'head', 'options']

    def get(self, request):
        qs = SalaryAdvance.objects.select_related('employee')
        if not (request.user.is_superuser or request.user.has_perm('hrm.approve_advance') or request.user.has_perm('hrm.view_salaryadvance')):
            qs = qs.filter(employee__user_id=request.user.pk)
        return Response({'results': SalaryAdvanceSerializer(qs[:100], many=True).data})

    def post(self, request):
        adv = request_advance(
            request.user,
            employee_id=int(request.data.get('employee')),
            requested_amount=request.data.get('requested_amount'),
            installment_count=int(request.data.get('installment_count') or 1),
            repayment_start_month=date.fromisoformat(str(request.data.get('repayment_start_month'))),
            currency=request.data.get('currency') or 'BDT',
            reason=request.data.get('reason') or '',
        )
        return Response(SalaryAdvanceSerializer(adv).data, status=status.HTTP_201_CREATED)


class SalaryAdvanceActionAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['post', 'options']
    action = ''

    def post(self, request, pk):
        if self.action == 'approve':
            adv = approve_advance(request.user, pk, approved_amount=request.data.get('approved_amount'))
            return Response(SalaryAdvanceSerializer(adv).data)
        if self.action == 'reject':
            adv = reject_advance(request.user, pk)
            return Response(SalaryAdvanceSerializer(adv).data)
        if self.action == 'mark-paid':
            adv = mark_advance_paid_external(request.user, pk, request.data.get('external_reference') or '')
            return Response(SalaryAdvanceSerializer(adv).data)
        req_key = request.data.get('request_key')
        if not req_key:
            raise ValidationError({'request_key': 'request_key UUID is required.'})
        adv, created = disburse_advance(request.user, pk, int(request.data.get('account')), UUID(str(req_key)))
        data = SalaryAdvanceSerializer(adv).data
        data['idempotent_replay'] = not created
        return Response(data)


class SalaryIncrementSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)

    class Meta:
        model = SalaryIncrement
        fields = ['id', 'employee', 'employee_name', 'compensation', 'previous_amount', 'new_amount', 'effective_date', 'reason', 'created_at']


class SalaryIncrementListAPIView(BaseAPIMixin, APIView):
    required_permissions = ('hrm.view_employeecompensation',)
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        qs = SalaryIncrement.objects.select_related('employee')
        if request.query_params.get('employee'):
            qs = qs.filter(employee_id=int(request.query_params['employee']))
        return Response({'results': SalaryIncrementSerializer(qs[:100], many=True).data})


class ManualPayrollVoucherSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)

    class Meta:
        model = ManualPayrollVoucher
        fields = [
            'id', 'employee', 'employee_name', 'payment_date', 'account', 'amount', 'method',
            'note', 'period', 'reference', 'payslip', 'reconciled_by', 'reconciled_at', 'created_at',
        ]


class ManualPayrollListCreateAPIView(BaseAPIMixin, APIView):
    required_permissions = ('hrm.manage_manual_payroll',)
    http_method_names = ['get', 'post', 'head', 'options']

    def get(self, request):
        qs = ManualPayrollVoucher.objects.select_related('employee')
        return Response({'results': ManualPayrollVoucherSerializer(qs[:100], many=True).data})

    def post(self, request):
        req_key = request.data.get('request_key')
        if not req_key:
            raise ValidationError({'request_key': 'request_key UUID is required.'})
        voucher, created = create_manual_voucher(
            request.user,
            employee_id=int(request.data.get('employee')),
            payment_date=date.fromisoformat(str(request.data.get('payment_date'))),
            account_id=int(request.data.get('account')),
            amount=request.data.get('amount'),
            method=request.data.get('method') or SalaryPayment.Method.CASH,
            note=request.data.get('note') or '',
            period_id=int(request.data['period']) if request.data.get('period') else None,
            reference=request.data.get('reference') or '',
            payslip_id=int(request.data['payslip']) if request.data.get('payslip') else None,
            request_key=UUID(str(req_key)),
        )
        data = ManualPayrollVoucherSerializer(voucher).data
        data['idempotent_replay'] = not created
        return Response(data, status=status.HTTP_201_CREATED)


class ManualPayrollReconcileAPIView(BaseAPIMixin, APIView):
    required_permissions = ('hrm.manage_manual_payroll',)
    http_method_names = ['post', 'options']

    def post(self, request, pk):
        v = reconcile_voucher_to_payslip(request.user, pk, int(request.data.get('payslip')))
        return Response(ManualPayrollVoucherSerializer(v).data)
