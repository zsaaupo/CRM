from collections import defaultdict
import csv
from decimal import Decimal
from io import BytesIO

from django.conf import settings
from django.db import transaction
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import serializers
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from common.view import BaseAPIMixin
from report.rendering import render_pdf
from report.services import money as formatted_money
from report.views import PDFRenderer
from .models import Payslip
from .payroll import FINALIZED, money
from .permissions import PAYROLL_DETAIL


class PayrollReportFilter(serializers.Serializer):
    month = serializers.DateField(required=False)
    currency = serializers.CharField(max_length=3, required=False)
    employee_code = serializers.CharField(max_length=40, required=False)
    export = serializers.ChoiceField(choices=['csv'], required=False)

    def validate(self, attrs):
        month = attrs.get('month')
        if month and month.day != 1:
            raise serializers.ValidationError({'month': 'Use the first calendar day of the month.'})
        if not month and not attrs.get('employee_code'):
            raise serializers.ValidationError('Choose a month or enter an employee code for salary history.')
        return attrs


AMOUNTS = ('gross_pay', 'total_deductions', 'net_pay')


def summarize(rows, fields):
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[field] for field in fields)].append(row)
    result = []
    for key, items in groups.items():
        totals = {field: money(sum(Decimal(item[field]) for item in items)) for field in AMOUNTS}
        result.append({**dict(zip(fields, key, strict=True)), 'employee_count': len(items), **totals})
    return result


def csv_text(value):
    # Spreadsheet programs can interpret user-provided names and codes as formulas.
    return f"'{value}" if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value


@method_decorator(never_cache, name='dispatch')
class PayrollReportAPIView(BaseAPIMixin, APIView):
    required_permissions = PAYROLL_DETAIL
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        filters = PayrollReportFilter(data=request.query_params)
        filters.is_valid(raise_exception=True)
        parameters = filters.validated_data
        slips = Payslip.objects.filter(payroll_run__status__in=FINALIZED).select_related('payroll_run__period').order_by(
            '-payroll_run__period__month', 'payroll_run__currency_id', 'employee_name', 'pk')
        if 'month' in parameters:
            slips = slips.filter(payroll_run__period__month=parameters['month'])
        if 'currency' in parameters:
            slips = slips.filter(payroll_run__currency_id=parameters['currency'].upper())
        if 'employee_code' in parameters:
            slips = slips.filter(employee_code__iexact=parameters['employee_code'])
        rows = [{
            'id': slip.pk, 'run_id': slip.payroll_run_id, 'status': slip.payroll_run.get_status_display(),
            'month': str(slip.payroll_run.period), 'currency': slip.payroll_run.currency_id,
            'employee_code': slip.employee_code, 'employee_name': slip.employee_name,
            'department': slip.department_name or 'Unassigned', **{field: money(getattr(slip, field)) for field in AMOUNTS},
        } for slip in slips]
        if parameters.get('export') == 'csv':
            response = HttpResponse(content_type='text/csv; charset=utf-8')
            response['Content-Disposition'] = 'attachment; filename="payroll-report.csv"'
            writer = csv.writer(response)
            fields = ('month', 'currency', 'employee_code', 'employee_name', 'department', 'status', *AMOUNTS)
            writer.writerow(fields)
            writer.writerows([csv_text(row[field]) for field in fields] for row in rows)
            return response
        return Response({'summary': summarize(rows, ('month', 'currency')),
                         'departments': summarize(rows, ('month', 'currency', 'department')), 'payslips': rows})


def payslip_document(pk):
    with transaction.atomic():
        slip = get_object_or_404(Payslip.objects.filter(payroll_run__status__in=FINALIZED).select_related(
            'payroll_run__period', 'payroll_run__approved_by',
        ).prefetch_related('lines'), pk=pk)
        run = slip.payroll_run
        return {
            'company': {'name': 'eManager', 'address': '', 'logo_path': '', **getattr(settings, 'REPORT_COMPANY', {})},
            'number': f'PS-{slip.pk}', 'employee': slip.employee_name, 'employee_code': slip.employee_code,
            'department': slip.department_name, 'designation': slip.designation_name,
            'month': run.period.month.strftime('%B %Y'), 'currency': run.currency_id,
            'status': run.get_status_display(), 'generated_at': timezone.localtime().strftime('%d %b %Y, %H:%M %Z'),
            'approved_by': run.approved_by.get_username() if run.approved_by_id else '',
            'attendance': slip.calculation_inputs.get('attendance_summary'),
            'rows': [{'name': line.name, 'code': line.code, 'type': line.get_type_display(),
                      'amount': formatted_money(line.amount)} for line in slip.lines.all()],
            'totals': [('Gross earnings', formatted_money(slip.gross_pay)),
                       ('Total deductions', formatted_money(slip.total_deductions)), ('Net pay', formatted_money(slip.net_pay))],
        }


@method_decorator(never_cache, name='dispatch')
class PayslipPDFAPIView(BaseAPIMixin, APIView):
    required_permissions = PAYROLL_DETAIL
    renderer_classes = [JSONRenderer, PDFRenderer]
    http_method_names = ['get', 'head', 'options']

    def get(self, request, pk):
        document = payslip_document(pk)
        response = FileResponse(BytesIO(render_pdf(document, 'report/payslip.html')), as_attachment=True,
                                filename=f'payslip-{pk}.pdf', content_type='application/pdf')
        response['X-Content-Type-Options'] = 'nosniff'
        response['Cache-Control'] = 'private, no-store, max-age=0'
        return response
