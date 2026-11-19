from decimal import Decimal

from django.db import IntegrityError, OperationalError, transaction
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import serializers, status
from rest_framework.generics import RetrieveAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from common.view import BaseAPIMixin, BaseListAPIView
from inventory.models import Currency
from .models import PayrollEvent, PayrollRun, Payslip, PayslipLine, SalaryPayment
from .payroll import FINALIZED, RECALCULABLE, PayrollBlocked, PayrollConflict, money, preview_payroll, save_payroll
from .payroll_workflow import RUN_ACTIONS, create_payment, payments_by_slip, resolve_payment, transition_run
from .permissions import PAYROLL_APPROVE, PAYROLL_CALCULATE, PAYROLL_DETAIL, PAYROLL_PAYMENT_VIEW, PAYROLL_PAYMENT_WRITE, PAYROLL_REVIEW


class PayrollInputSerializer(serializers.Serializer):
    month = serializers.DateField()
    currency = serializers.SlugRelatedField(slug_field='code', queryset=Currency.objects.filter(is_active=True))

    def validate_month(self, value):
        if value.day != 1:
            raise serializers.ValidationError('Use the first calendar day of the payroll month.')
        return value


class PayrollSaveSerializer(PayrollInputSerializer):
    preview_token = serializers.RegexField(r'\A[0-9a-f]{64}\Z')


class PayslipLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayslipLine
        fields = ['code', 'name', 'type', 'amount', 'calculation_inputs']


class PayslipSerializer(serializers.ModelSerializer):
    lines = PayslipLineSerializer(many=True, read_only=True)

    class Meta:
        model = Payslip
        fields = ['id', 'employee', 'employee_code', 'employee_name', 'department_name', 'gross_pay', 'total_deductions', 'net_pay',
                  'calculation_inputs', 'warnings', 'lines']


class PayrollRunSerializer(serializers.ModelSerializer):
    month = serializers.DateField(source='period.month', format='%Y-%m', read_only=True)
    currency = serializers.CharField(source='currency_id', read_only=True)
    status_name = serializers.CharField(source='get_status_display', read_only=True)
    calculated_by = serializers.SerializerMethodField()

    def get_calculated_by(self, obj):
        return obj.calculated_by.get_username()

    class Meta:
        model = PayrollRun
        fields = ['id', 'month', 'currency', 'status', 'status_name', 'calculated_at', 'calculated_by']


class PayrollRunDetailSerializer(PayrollRunSerializer):
    payslips = PayslipSerializer(many=True, read_only=True)

    class Meta(PayrollRunSerializer.Meta):
        fields = [*PayrollRunSerializer.Meta.fields, 'payslips', 'reviewed_at', 'approved_at']

    def to_representation(self, run):
        data = super().to_representation(run)
        user = self.context['request'].user
        can_view_payment = user.has_perms(PAYROLL_PAYMENT_VIEW)
        events = run.events.all()
        if not can_view_payment:
            events = events.exclude(action__startswith='payment_')
        data['events'] = PayrollEventSerializer(events.select_related('actor'), many=True).data
        data['available_actions'] = [action for action, (allowed, _, _) in RUN_ACTIONS.items() if run.status in allowed
                                     and user.has_perms(PAYROLL_APPROVE if action == 'approve' else PAYROLL_REVIEW)]
        data['published'] = run.status in FINALIZED
        data['can_pay'] = run.status in ('approved', 'payment_processing') and user.has_perms(PAYROLL_PAYMENT_WRITE)
        if can_view_payment:
            paid, pending = payments_by_slip(run)
            for slip_data in data['payslips']:
                pk = slip_data['id']
                total = Decimal(slip_data['net_pay'])
                slip_data.update(paid_amount=money(paid.get(pk, 0)), pending_amount=money(pending.get(pk, 0)),
                                 available_amount=money(total - paid.get(pk, 0) - pending.get(pk, 0)))
            attempts = SalaryPayment.objects.filter(payslip__payroll_run=run).select_related('resolved_by')
            data['payments'] = SalaryPaymentSerializer(attempts, many=True).data
        return data


class PayrollEventSerializer(serializers.ModelSerializer):
    actor = serializers.SlugRelatedField(slug_field='username', read_only=True)

    class Meta:
        model = PayrollEvent
        fields = ['action', 'actor', 'note', 'data', 'created_at']


class SalaryPaymentSerializer(serializers.ModelSerializer):
    resolved_by = serializers.SlugRelatedField(slug_field='username', read_only=True)
    status_name = serializers.CharField(source='get_status_display', read_only=True)
    method_name = serializers.CharField(source='get_method_display', read_only=True)

    class Meta:
        model = SalaryPayment
        fields = ['id', 'payslip', 'amount', 'method', 'method_name', 'status', 'status_name', 'reference', 'paid_on',
                  'failure_reason', 'created_at', 'create_by', 'resolved_at', 'resolved_by']


class PayrollRunListAPIView(BaseListAPIView):
    queryset = PayrollRun.objects.select_related('period', 'calculated_by')
    serializer_class = PayrollRunSerializer
    search_fields = ['period__month', 'currency__code', 'status']
    ordering = ['-period__month', 'currency_id', 'pk']


@method_decorator(never_cache, name='dispatch')
class PayrollRunDetailAPIView(BaseAPIMixin, RetrieveAPIView):
    queryset = PayrollRun.objects.select_related('period', 'calculated_by').prefetch_related('payslips__lines')
    serializer_class = PayrollRunDetailSerializer
    required_permissions = PAYROLL_DETAIL
    http_method_names = ['get', 'head', 'options']

    @transaction.atomic
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


@method_decorator(never_cache, name='dispatch')
class PayrollOptionsAPIView(BaseAPIMixin, APIView):
    required_permissions = PAYROLL_CALCULATE
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        currencies = Currency.objects.filter(is_active=True).order_by('name', 'pk').values_list('code', 'name')
        return Response({'currency': [{'value': code, 'label': f'{name} ({code})'} for code, name in currencies]})


@method_decorator(never_cache, name='dispatch')
class PayrollPreviewAPIView(BaseAPIMixin, APIView):
    required_permissions = PAYROLL_CALCULATE
    http_method_names = ['post', 'options']

    @transaction.atomic
    def post(self, request):
        serializer = PayrollInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        month = serializer.validated_data['month']
        currency = serializer.validated_data['currency'].code
        preview = preview_payroll(month, currency)
        if PayrollRun.objects.filter(period__month=month, currency_id=currency).exclude(
            status__in=RECALCULABLE,
        ).exists():
            preview['blockers'].append('This payroll run is locked.')
            preview.update(can_calculate=False, preview_token=None)
        return Response(preview)


@method_decorator(never_cache, name='dispatch')
class PayrollCalculateAPIView(BaseAPIMixin, APIView):
    required_permissions = PAYROLL_CALCULATE
    http_method_names = ['post', 'options']

    def post(self, request):
        try:
            serializer = PayrollSaveSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data
            run, created = save_payroll(data['month'], data['currency'].code, data['preview_token'], request.user)
        except PayrollBlocked as exc:
            return Response({'detail': str(exc), 'preview': exc.preview}, status=status.HTTP_409_CONFLICT)
        except PayrollConflict as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except OperationalError as exc:
            if 'locked' not in str(exc).lower() and 'deadlock' not in str(exc).lower():
                raise
            return Response({'detail': 'Payroll data is busy. Preview the period and try again.'},
                            status=status.HTTP_409_CONFLICT)
        except IntegrityError:
            return Response({'detail': 'Payroll data changed while saving. Preview the period and try again.'},
                            status=status.HTTP_409_CONFLICT)
        return Response(PayrollRunSerializer(run).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@method_decorator(never_cache, name='dispatch')
class PayrollActionAPIView(BaseAPIMixin, APIView):
    http_method_names = ['post', 'options']

    def handle_exception(self, exc):
        if isinstance(exc, PayrollConflict):
            return Response({'detail': str(exc)}, status=409)
        if isinstance(exc, IntegrityError) or (isinstance(exc, OperationalError) and
            any(word in str(exc).lower() for word in ('locked', 'deadlock'))):
            return Response({'detail': 'Payroll data changed during this operation. Refresh and try again.'}, status=409)
        return super().handle_exception(exc)


class PayrollRunTransitionAPIView(PayrollActionAPIView):
    action = ''

    @property
    def required_permissions(self):
        return PAYROLL_APPROVE if self.action == 'approve' else PAYROLL_REVIEW

    def post(self, request, pk):
        note = serializers.CharField(max_length=1000, allow_blank=self.action not in ('reject', 'cancel')).run_validation(
            request.data.get('note', ''),
        )
        return Response(PayrollRunSerializer(transition_run(pk, self.action, request.user, note)).data)


class SalaryPaymentInputSerializer(serializers.Serializer):
    payslip = serializers.IntegerField(min_value=1, max_value=2**63 - 1)
    amount = serializers.DecimalField(min_value=Decimal('0.0001'), max_digits=18, decimal_places=4)
    method = serializers.ChoiceField(choices=SalaryPayment.Method.choices)
    request_key = serializers.UUIDField()


class SalaryPaymentOutcomeSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['succeeded', 'failed'])
    reference = serializers.CharField(max_length=120, allow_blank=True, default='')
    paid_on = serializers.DateField(allow_null=True, default=None)
    failure_reason = serializers.CharField(max_length=255, allow_blank=True, default='')

    def validate(self, attrs):
        if attrs['status'] == 'succeeded':
            if not attrs['reference'] or attrs['paid_on'] is None:
                raise serializers.ValidationError('A successful payment requires a reference and payment date.')
            if attrs['paid_on'] > timezone.localdate():
                raise serializers.ValidationError({'paid_on': 'Use the date of the actual payment.'})
            if attrs['failure_reason']:
                raise serializers.ValidationError({'failure_reason': 'Clear the failure reason for a successful payment.'})
        elif not attrs['failure_reason'] or attrs['paid_on'] is not None:
            raise serializers.ValidationError('A failed attempt requires a failure reason and an empty payment date.')
        return attrs


class SalaryPaymentCreateAPIView(PayrollActionAPIView):
    required_permissions = PAYROLL_PAYMENT_WRITE

    def post(self, request, pk):
        serializer = SalaryPaymentInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment, created = create_payment(pk, request.user, **serializer.validated_data)
        return Response(SalaryPaymentSerializer(payment).data, status=201 if created else 200)


class SalaryPaymentResolveAPIView(PayrollActionAPIView):
    required_permissions = PAYROLL_PAYMENT_WRITE

    def post(self, request, pk, payment_id):
        serializer = SalaryPaymentOutcomeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment = resolve_payment(pk, payment_id, request.user, **serializer.validated_data)
        return Response(SalaryPaymentSerializer(payment).data)
