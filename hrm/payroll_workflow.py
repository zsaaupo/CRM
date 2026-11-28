from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import NotFound

from audit_log.service import record_activity
from .leave_balances import lock_employees, post_debits
from .models import LoanInstallment, PayrollEvent, PayrollPeriod, PayrollRun, Payslip, SalaryPayment
from .payroll import FINALIZED, PayrollConflict, preview_payroll


RUN = PayrollRun.Status
PAYMENT = SalaryPayment.Status
RUN_ACTIONS = {
    'review': ({RUN.CALCULATED}, RUN.REVIEWED, 'reviewed'),
    'return': ({RUN.REVIEWED}, RUN.CALCULATED, 'returned'),
    'reject': ({RUN.CALCULATED, RUN.REVIEWED}, RUN.REJECTED, 'rejected'),
    'cancel': ({RUN.CALCULATED, RUN.REVIEWED, RUN.REJECTED}, RUN.CANCELLED, 'cancelled'),
    'approve': ({RUN.REVIEWED}, RUN.APPROVED, 'approved'),
}


def lock_run(pk):
    # The write serializes payment reservations and status transitions on SQLite.
    PayrollPeriod.objects.filter(runs__pk=pk).update(month=F('month'))
    rows = PayrollRun.objects.filter(pk=pk)
    if not rows.update(updated_at=F('updated_at')):
        raise NotFound('Payroll run not found.')
    return rows.select_related('period').get()


def record_event(run, actor, action, note='', **data):
    PayrollEvent.objects.create(run=run, actor=actor, action=action, note=note, data=data)


def payments_by_slip(run):
    paid = {}
    pending = {}
    for slip_id, status, amount in SalaryPayment.objects.filter(payslip__payroll_run=run).exclude(
        status=PAYMENT.FAILED,
    ).values_list('payslip_id', 'status', 'amount'):
        bucket = paid if status == PAYMENT.SUCCEEDED else pending
        bucket[slip_id] = bucket.get(slip_id, Decimal('0')) + amount
    return paid, pending


def recover_installments(slip, actor):
    for line in slip.lines.all():
        for inputs in line.calculation_inputs:
            installment_id = inputs.get('installment_id')
            if installment_id:
                updated = LoanInstallment.objects.filter(pk=installment_id, loan__employee_id=slip.employee_id,
                    loan__currency_id=slip.payroll_run.currency_id, month=slip.payroll_run.period.month,
                    amount=line.amount, recovered_line__isnull=True).update(
                        recovered_line=line, recovered_at=timezone.now(), updated_at=timezone.now())
                if not updated and not LoanInstallment.objects.filter(pk=installment_id, recovered_line=line).exists():
                    raise PayrollConflict('A loan installment changed after payroll approval.')
                if updated:
                    record_activity(LoanInstallment.objects.get(pk=installment_id), 'recovered', actor=actor)


@transaction.atomic
def transition_run(pk, action, actor, note=''):
    run = lock_run(pk)
    if run.period.attendance_snapshot:
        if action == 'approve' and run.status in FINALIZED:
            return run
        lock_employees(run.payslips.order_by().values('employee_id'))
    allowed, destination, event = RUN_ACTIONS[action]
    if run.status not in allowed:
        raise PayrollConflict('This action is unavailable for the current payroll status.')
    if action in ('review', 'approve'):
        current = preview_payroll(run.period.month, run.currency_id)
        if not current['can_calculate'] or current['preview_token'] != run.input_digest:
            raise PayrollConflict('Payroll inputs have changed. Return the run to calculation and recalculate it.')
    run.status = destination
    run.modified_by = actor.get_username()
    if action == 'review':
        run.reviewed_by = actor
        run.reviewed_at = timezone.now()
    elif action == 'return':
        run.reviewed_by = None
        run.reviewed_at = None
    elif action == 'approve':
        run.approved_by = actor
        run.approved_at = timezone.now()
    run.save()
    record_event(run, actor, event, note)
    if action == 'approve':
        if run.period.attendance_snapshot:
            for slip in run.payslips.select_related('payroll_run').order_by('employee_id'):
                post_debits(slip.calculation_inputs.get('leave_coverage', ()), actor.get_username(), payslip=slip)
        for slip in run.payslips.filter(net_pay=0):
            recover_installments(slip, actor)
        if not run.payslips.filter(net_pay__gt=0).exists():
            run.status = RUN.PAID
            run.save(update_fields=['status', 'updated_at'])
            record_event(run, actor, 'paid', 'No cash payment was due.')
    return run


@transaction.atomic
def create_payment(run_id, actor, *, payslip, amount, method, request_key):
    run = lock_run(run_id)
    existing = SalaryPayment.objects.filter(request_key=request_key).first()
    if existing:
        if (existing.payslip_id, existing.amount, existing.method) != (payslip, amount, method) or not run.payslips.filter(pk=payslip).exists():
            raise PayrollConflict('This payment request key was already used for different details.')
        return existing, False
    if run.status not in (RUN.APPROVED, RUN.PAYMENT_PROCESSING):
        raise PayrollConflict('Salary payments require an approved run with an outstanding balance.')
    slip = Payslip.objects.filter(payroll_run=run, pk=payslip).first()
    if slip is None:
        raise NotFound('Payslip not found in this payroll run.')
    reserved = slip.payments.exclude(status=PAYMENT.FAILED).values_list('amount', flat=True)
    if sum(reserved, amount) > slip.net_pay:
        raise PayrollConflict('This amount exceeds the unpaid balance after pending attempts.')
    username = actor.get_username()
    payment = SalaryPayment.objects.create(payslip=slip, amount=amount, method=method, request_key=request_key,
                                          create_by=username, modified_by=username)
    payment.refresh_from_db(fields=['amount'])
    if payment.amount != amount:
        raise PayrollConflict('The database could not record this payment amount exactly.')
    if run.status == RUN.APPROVED:
        run.status = RUN.PAYMENT_PROCESSING
        run.modified_by = username
        run.save(update_fields=['status', 'modified_by', 'updated_at'])
    record_event(run, actor, 'payment_started', payment_id=payment.pk, amount=str(amount))
    return payment, True


@transaction.atomic
def resolve_payment(run_id, payment_id, actor, status, reference='', paid_on=None, failure_reason=''):
    run = lock_run(run_id)
    payment = SalaryPayment.objects.select_related('payslip__payroll_run__period').filter(
        pk=payment_id, payslip__payroll_run=run,
    ).first()
    if payment is None:
        raise NotFound('Payment not found in this payroll run.')
    if payment.status != PAYMENT.PENDING:
        if (payment.status, payment.reference, payment.paid_on, payment.failure_reason) == (status, reference, paid_on, failure_reason):
            return payment
        raise PayrollConflict('This payment attempt already has a recorded outcome.')
    if run.status != RUN.PAYMENT_PROCESSING:
        raise PayrollConflict('This payroll run cannot receive a payment outcome.')
    if status == PAYMENT.SUCCEEDED and payment.payslip.payments.filter(status=PAYMENT.SUCCEEDED, reference=reference).exists():
        raise PayrollConflict('This payment reference already succeeded for the payslip.')
    payment.status = status
    payment.reference = reference
    payment.paid_on = paid_on
    payment.failure_reason = failure_reason
    payment.resolved_by = actor
    payment.resolved_at = timezone.now()
    payment.modified_by = actor.get_username()
    payment.save()
    record_event(run, actor, f'payment_{status}', failure_reason, payment_id=payment.pk, reference=reference)
    if status == PAYMENT.SUCCEEDED:
        paid, _ = payments_by_slip(run)
        if paid[payment.payslip_id] == payment.payslip.net_pay:
            recover_installments(payment.payslip, actor)
        if all(paid.get(pk, 0) == net for pk, net in run.payslips.values_list('pk', 'net_pay')):
            run.status = RUN.PAID
            run.modified_by = actor.get_username()
            run.save(update_fields=['status', 'modified_by', 'updated_at'])
            record_event(run, actor, 'paid')
    return payment
