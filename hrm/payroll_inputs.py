"""The existing HRM CRUD conventions for approved payroll inputs and loan schedules."""

from decimal import Decimal, localcontext

from django.core.exceptions import ValidationError as ModelValidationError
from django.db import OperationalError, transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from common.serializers import AUDIT_FIELDS, CatalogSerializer
from common.view import BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView, BaseUpdateAPIView, RecordInUse
from inventory.models import Currency
from .models import (
    Employee, EmployeeLoan, LoanInstallment, PayrollAdjustment, PayrollDeductionRule, PayrollPeriod, PayrollRun,
    Payslip, SalaryComponent, SalaryStructure,
)
from .payroll import FINALIZED, money, round_money
from .permissions import PAYROLL_DETAIL, PAYROLL_REVIEW_ADJUSTMENT
from .services import lock_salary_record


class PayrollAdjustmentSerializer(CatalogSerializer):
    currency = serializers.SlugRelatedField(slug_field='code', queryset=Currency.objects.all())
    amount = serializers.DecimalField(max_digits=18, decimal_places=4, required=False, allow_null=True)
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    component_name = serializers.CharField(source='component.name', read_only=True)
    source_name = serializers.CharField(source='get_source_display', read_only=True)
    status_name = serializers.CharField(source='get_status_display', read_only=True)
    reviewed_by = serializers.SlugRelatedField(slug_field='username', read_only=True)
    is_locked = serializers.SerializerMethodField()

    def get_is_locked(self, record):
        return record.status != PayrollAdjustment.Status.DRAFT

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get('request')
        if not request or not request.user.has_perms(PAYROLL_DETAIL):
            fields['correction_of'].queryset = Payslip.objects.none()
        return fields

    def validate(self, attrs):
        source = attrs.get('source', getattr(self.instance, 'source', PayrollAdjustment.Source.MANUAL))
        if source == PayrollAdjustment.Source.OVERTIME:
            keys = ('overtime_minutes', 'hourly_rate', 'multiplier')
            values = [attrs.get(key, getattr(self.instance, key, None)) for key in keys]
            if not all(values):
                raise ValidationError({'overtime_minutes': 'Enter minutes, hourly rate, and multiplier.'})
            minutes, hourly_rate, multiplier = values
            with localcontext() as ctx:
                ctx.prec = 50
                attrs['amount'] = self.fields['amount'].run_validation(round_money(minutes * hourly_rate * multiplier / 60))
        if attrs.get('amount', getattr(self.instance, 'amount', None)) is None:
            raise ValidationError({'amount': 'Enter an amount.'})
        return super().validate(attrs)

    class Meta:
        model = PayrollAdjustment
        fields = ['id', 'employee', 'employee_name', 'currency', 'component', 'component_name', 'effective_on',
                  'source', 'source_name', 'amount', 'reason', 'overtime_minutes', 'hourly_rate', 'multiplier',
                  'correction_of', 'status', 'status_name', 'is_locked', 'reviewed_by', 'reviewed_at', *AUDIT_FIELDS[1:]]
        read_only_fields = [*AUDIT_FIELDS, 'status', 'reviewed_at']


class PayrollDeductionRuleSerializer(CatalogSerializer):
    salary_structure_name = serializers.CharField(source='salary_structure.name', read_only=True)
    component_name = serializers.CharField(source='component.name', read_only=True)
    method_name = serializers.CharField(source='get_method_display', read_only=True)

    class Meta:
        model = PayrollDeductionRule
        fields = ['id', 'salary_structure', 'salary_structure_name', 'component', 'component_name', 'method',
                  'method_name', 'value', 'effective_from', 'effective_to', 'reference', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS


class EmployeeLoanSerializer(CatalogSerializer):
    currency = serializers.SlugRelatedField(slug_field='code', queryset=Currency.objects.all())
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    balance = serializers.SerializerMethodField()

    def get_balance(self, loan):
        return money(loan.principal - sum(row.amount for row in loan.installments.all() if row.recovered_line_id))

    class Meta:
        model = EmployeeLoan
        fields = ['id', 'employee', 'employee_name', 'currency', 'reference', 'principal', 'balance', 'disbursed_on',
                  'notes', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS


class LoanInstallmentSerializer(CatalogSerializer):
    loan_reference = serializers.CharField(source='loan.reference', read_only=True)
    currency = serializers.CharField(source='loan.currency_id', read_only=True)
    recovered = serializers.SerializerMethodField()

    def validate_month(self, value):
        if value.day != 1:
            raise serializers.ValidationError('Use the first day of the payroll month.')
        return value

    def get_recovered(self, row):
        return row.recovered_line_id is not None

    class Meta:
        model = LoanInstallment
        fields = ['id', 'loan', 'loan_reference', 'currency', 'month', 'amount', 'recovered', 'recovered_at', *AUDIT_FIELDS[1:]]
        read_only_fields = [*AUDIT_FIELDS, 'recovered_at']


def guard_open_period(month, currency):
    month = month.replace(day=1)
    PayrollPeriod.objects.filter(month=month).update(month=F('month'))
    if PayrollRun.objects.filter(period__month=month, currency_id=currency, status__in=FINALIZED).exists():
        raise RecordInUse('This payroll period and currency have already been approved.')


def check_input_write(model, instance, changes):
    if model is PayrollAdjustment:
        if instance:
            if instance.status != PayrollAdjustment.Status.DRAFT:
                raise RecordInUse('Only draft payroll adjustments can be edited or deleted.')
            guard_open_period(instance.effective_on, instance.currency_id)
        if changes:
            currency = changes.get('currency')
            guard_open_period(changes.get('effective_on', getattr(instance, 'effective_on', None)),
                              currency.code if currency else instance.currency_id)
    elif model is PayrollDeductionRule:
        structures = {instance.salary_structure_id} if instance else set()
        if 'salary_structure' in changes:
            structures.add(changes['salary_structure'].pk)
        for pk in sorted(structures):
            lock_salary_record(SalaryStructure, pk)
        if changes:
            component = changes.get('component')
            lock_salary_record(SalaryComponent, component.pk if component else instance.component_id)
    elif model is EmployeeLoan and instance and instance.installments.exists():
        fields = ('employee', 'currency', 'reference', 'principal', 'disbursed_on')
        if any(key in changes and changes[key] != getattr(instance, key) for key in fields):
            raise RecordInUse('Loan terms are locked once installments are scheduled.')
    elif model is LoanInstallment:
        loan = changes.get('loan', getattr(instance, 'loan', None))
        loan_ids = {item for item in (getattr(instance, 'loan_id', None), loan.pk if loan else None) if item}
        for pk in sorted(loan_ids):
            lock_salary_record(EmployeeLoan, pk)
        if instance:
            if instance.recovered_line_id:
                raise RecordInUse('This installment has been recovered through payroll.')
            guard_open_period(instance.month, instance.loan.currency_id)
        if changes:
            guard_open_period(changes.get('month', getattr(instance, 'month', None)), loan.currency_id)


class PayrollInputWriteMixin:
    def handle_exception(self, exc):
        if isinstance(exc, OperationalError) and any(word in str(exc).lower() for word in ('locked', 'deadlock')):
            exc = RecordInUse('Payroll data is being updated. Please retry.')
        return super().handle_exception(exc)

    @transaction.atomic
    def save_record(self, serializer, **audit):
        model = self.queryset.model
        if serializer.instance:
            serializer.instance = lock_salary_record(model, serializer.instance.pk)
        check_input_write(model, serializer.instance, serializer.validated_data)
        serializer.validated_data.update(serializer.validate(dict(serializer.validated_data)))
        numeric_fields = {key: value for key, value in serializer.validated_data.items() if isinstance(value, Decimal)}
        super().save_record(serializer, **audit)
        if numeric_fields:
            serializer.instance.refresh_from_db(fields=list(numeric_fields))
            if any(getattr(serializer.instance, key) != value for key, value in numeric_fields.items()):
                raise ValidationError('The database could not store the entered amounts exactly.')

    @transaction.atomic
    def perform_destroy(self, instance):
        current = lock_salary_record(self.queryset.model, instance.pk)
        check_input_write(self.queryset.model, current, {})
        return super().perform_destroy(current)


class InputListCreateAPIView(PayrollInputWriteMixin, BaseListCreateAPIView):
    response_key = ''


class InputDetailAPIView(PayrollInputWriteMixin, BaseDetailAPIView):
    pass


class InputUpdateAPIView(PayrollInputWriteMixin, BaseUpdateAPIView):
    pass


def choices(queryset, field='pk', label_field='name'):
    return [{'value': key, 'label': label} for key, label in queryset.order_by(label_field, 'pk').values_list(field, label_field)]


def enum_choices(field):
    return [{'value': key, 'label': label} for key, label in field.choices]


@method_decorator(never_cache, name='dispatch')
class PayrollInputOptionsAPIView(BaseAPIMixin, APIView):
    model = None
    http_method_names = ['get', 'head', 'options']

    @property
    def required_permissions(self):
        name = self.model._meta.model_name
        return (f'hrm.view_{name}', f'hrm.change_{name}') if 'pk' in self.kwargs else (f'hrm.add_{name}',)

    def get(self, request, pk=None):
        current = get_object_or_404(self.model, pk=pk) if pk is not None else None
        data = {}
        if self.model in (PayrollAdjustment, EmployeeLoan):
            data['employee'] = [{'value': employee.pk, 'label': f'{employee.full_name} ({employee.employee_code})'}
                for employee in Employee.objects.order_by('full_name', 'pk')]
            if current and current.employee.is_archived:
                data['employee'].append({'value': current.employee_id, 'label': 'Archived employee (existing)'})
            data['currency'] = choices(Currency.objects.filter(Q(is_active=True) | Q(code=getattr(current, 'currency_id', None))),
                                       'code', 'code')
        if self.model is PayrollAdjustment:
            data['component'] = choices(SalaryComponent.objects.filter(Q(is_active=True) | Q(pk=getattr(current, 'component_id', None))))
            data['source'] = enum_choices(PayrollAdjustment.Source)
            slips = Payslip.objects.filter(payroll_run__status__in=FINALIZED).select_related('payroll_run__period')
            data['correction_of'] = [{'value': slip.pk,
                'label': f'{slip.employee_name} - {slip.payroll_run.period} ({slip.payroll_run.currency_id})'}
                for slip in slips] if request.user.has_perms(PAYROLL_DETAIL) else []
        elif self.model is PayrollDeductionRule:
            data['salary_structure'] = choices(SalaryStructure.objects.filter(Q(is_active=True) | Q(pk=getattr(current, 'salary_structure_id', None))))
            data['component'] = choices(SalaryComponent.objects.filter(Q(is_active=True, type=SalaryComponent.Kind.DEDUCTION)
                                                                       | Q(pk=getattr(current, 'component_id', None))))
            data['method'] = enum_choices(PayrollDeductionRule.Method)
        elif self.model is LoanInstallment:
            data['loan'] = [{'value': item.pk, 'label': f'{item.reference} - {item.employee.full_name} ({item.currency_id})'}
                            for item in EmployeeLoan.objects.select_related('employee').order_by('reference')]
        return Response(data)


@method_decorator(never_cache, name='dispatch')
class PayrollAdjustmentReviewAPIView(BaseAPIMixin, APIView):
    required_permissions = PAYROLL_REVIEW_ADJUSTMENT
    http_method_names = ['post', 'options']

    @transaction.atomic
    def post(self, request, pk):
        decision = serializers.ChoiceField(choices=['approved', 'cancelled']).run_validation(request.data.get('status'))
        record = lock_salary_record(PayrollAdjustment, pk)
        guard_open_period(record.effective_on, record.currency_id)
        if record.status == decision:
            return Response(PayrollAdjustmentSerializer(record).data)
        if record.status == PayrollAdjustment.Status.CANCELLED or (
            decision == PayrollAdjustment.Status.APPROVED and record.status != PayrollAdjustment.Status.DRAFT
        ):
            raise RecordInUse('This adjustment cannot be reviewed again.')
        if decision == PayrollAdjustment.Status.APPROVED:
            component = lock_salary_record(SalaryComponent, record.component_id)
            try:
                record.full_clean(exclude=AUDIT_FIELDS[1:])
            except ModelValidationError as exc:
                raise ValidationError(exc.message_dict) from exc
            record.approved_type = component.type
        record.status = decision
        record.reviewed_by = request.user
        record.reviewed_at = timezone.now()
        record.modified_by = request.user.get_username()
        record.save()
        return Response(PayrollAdjustmentSerializer(record).data)


def input_urls(resources):
    serializers_by_model = {
        PayrollAdjustment: (PayrollAdjustmentSerializer, PayrollAdjustment.objects.select_related('employee', 'component', 'reviewed_by'),
                            ['employee__full_name', 'component__name', 'reason', 'source']),
        PayrollDeductionRule: (PayrollDeductionRuleSerializer, PayrollDeductionRule.objects.select_related('salary_structure', 'component'),
                               ['salary_structure__name', 'component__name', 'reference']),
        EmployeeLoan: (EmployeeLoanSerializer, EmployeeLoan.objects.select_related('employee').prefetch_related('installments'),
                       ['reference', 'employee__full_name']),
        LoanInstallment: (LoanInstallmentSerializer, LoanInstallment.objects.select_related('loan'), ['loan__reference']),
    }
    routes = []
    for resource in resources:
        serializer, queryset, fields = serializers_by_model[resource.model]
        common = {'queryset': queryset, 'serializer_class': serializer}
        listing = {**common, 'search_fields': fields, 'ordering': resource.model._meta.ordering}
        routes += [
            path(f'{resource.path}/', InputListCreateAPIView.as_view(**listing, response_key=resource.key.replace('-', '_')),
                 name=f'{resource.key}-list'),
            path(f'{resource.path}/list/', BaseListAPIView.as_view(**listing), name=f'{resource.key}-list-view'),
            path(f'{resource.path}/options/', PayrollInputOptionsAPIView.as_view(model=resource.model), name=f'{resource.key}-options'),
            path(f'{resource.path}/<int:pk>/options/', PayrollInputOptionsAPIView.as_view(model=resource.model),
                 name=f'{resource.key}-edit-options'),
            path(f'{resource.path}/<int:pk>/', InputDetailAPIView.as_view(**common), name=f'{resource.key}-detail'),
            path(f'{resource.path}/<int:pk>/update/', InputUpdateAPIView.as_view(**common), name=f'{resource.key}-update'),
        ]
    return routes
