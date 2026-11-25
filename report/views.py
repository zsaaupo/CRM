from io import BytesIO

from django.http import FileResponse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.views import APIView

from common.view import BaseAPIMixin
from .catalog import REPORTS
from .rendering import render_pdf
from .services import snapshot


class PDFRenderer(BaseRenderer):
    media_type = 'application/pdf'
    format = 'pdf'
    charset = None

    def render(self, data, accepted_media_type=None, renderer_context=None):
        # Successful documents use FileResponse; errors stay JSON for PDF-only clients too.
        renderer_context['response']['Content-Type'] = 'application/json'
        return JSONRenderer().render(data, renderer_context=renderer_context)


@method_decorator(never_cache, name='dispatch')
class DocumentPDFAPIView(BaseAPIMixin, APIView):
    role_permissions_only = True
    renderer_classes = [JSONRenderer, PDFRenderer]
    http_method_names = ['get', 'head', 'options']
    kind = ''

    @property
    def required_permissions(self):
        return REPORTS[self.kind].permissions

    def get(self, request, pk):
        document = snapshot(self.kind, pk, request.user)
        response = FileResponse(BytesIO(render_pdf(document)), as_attachment=True,
            filename=document['filename'], content_type='application/pdf')
        response['Cache-Control'] = 'private, no-store, max-age=0'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class PurchasePDFAPIView(DocumentPDFAPIView):
    kind = 'purchase'


class SalePDFAPIView(DocumentPDFAPIView):
    kind = 'sale'


class QuotationPDFAPIView(DocumentPDFAPIView):
    kind = 'quotation'


class TransferPDFAPIView(DocumentPDFAPIView):
    kind = 'transfer'



from django.http import HttpResponse
from rest_framework import serializers as _drf_serializers
from rest_framework.authentication import SessionAuthentication as _SessionAuth
from rest_framework.permissions import IsAuthenticated as _IsAuth
from rest_framework.views import APIView as _APIView
from account.permissions import HasRequiredPermissions as _HasPerms
from inventory.barcodes import generate_barcodes_pdf as _generate_barcodes_pdf


class BarcodePDFItemSerializer(_drf_serializers.Serializer):
    product = _drf_serializers.IntegerField(min_value=1)
    copies = _drf_serializers.IntegerField(min_value=1, max_value=500, default=1)


class BarcodePDFRequestSerializer(_drf_serializers.Serializer):
    items = BarcodePDFItemSerializer(many=True, allow_empty=False)
    layout = _drf_serializers.CharField(required=False, default='a4_24')
    layout_id = _drf_serializers.CharField(required=False, allow_blank=True, default='')
    fields = _drf_serializers.DictField(required=False, default=dict)


class BarcodePDFAPIView(_APIView):
    authentication_classes = [_SessionAuth]
    permission_classes = [_IsAuth, _HasPerms]
    required_permissions = ('inventory.view_product',)
    http_method_names = ['post', 'options']

    def post(self, request):
        ser = BarcodePDFRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        layout = ser.validated_data.get('layout_id') or ser.validated_data.get('layout') or 'a4_24'
        pdf = _generate_barcodes_pdf(
            ser.validated_data['items'],
            layout=layout,
            fields=ser.validated_data.get('fields'),
        )
        resp = HttpResponse(pdf, content_type='application/pdf')
        resp['Content-Disposition'] = 'inline; filename="barcodes.pdf"'
        return resp



class _RawPDFNegotiation:
    def select_parser(self, request, parsers):
        return parsers[0] if parsers else None

    def select_renderer(self, request, renderers, format_suffix=None):
        return (renderers[0] if renderers else None, 'application/pdf')


class PosReceiptPDFAPIView(_APIView):
    authentication_classes = [_SessionAuth]
    permission_classes = [_IsAuth, _HasPerms]
    content_negotiation_class = _RawPDFNegotiation
    required_permissions = ('sale.view_salesorder',)
    http_method_names = ['get', 'head', 'options']

    def get(self, request, pk):
        from django.conf import settings as _settings
        from django.shortcuts import get_object_or_404
        from django.template.loader import render_to_string
        from common.settings_store import get_general
        from report.rendering import ReportUnavailable, asset_fetcher, pdf_engine
        from sale.models import SalesOrder
        from warehouse.scope import scoped_queryset, warehouse_scope

        scope = warehouse_scope(request.user)
        order = get_object_or_404(
            scoped_queryset(SalesOrder.objects.select_related('customer', 'warehouse').prefetch_related('items__product'), scope),
            pk=pk,
        )
        fmt = (request.query_params.get('format') or '80mm').lower()
        if fmt not in ('a4', '58mm', '80mm'):
            fmt = '80mm'
        company_name = get_general().company_name or 'eManager'
        company = {'name': company_name, 'address': '', 'phone': '', 'email': '', 'logo_path': ''}
        company.update(getattr(_settings, 'REPORT_COMPANY', {}))
        try:
            CSS, HTML, FontConfiguration = pdf_engine()
            fonts = FontConfiguration()
            html = render_to_string('report/pos_receipt.html', {
                'order': order,
                'items': list(order.items.all()),
                'company_name': company_name,
                'fmt': fmt,
            })
            pdf = HTML(string=html, base_url='report-asset:///', url_fetcher=asset_fetcher(company)).write_pdf(font_config=fonts)
        except (ImportError, OSError, ValueError, RuntimeError):
            raise ReportUnavailable() from None
        resp = HttpResponse(pdf, content_type='application/pdf')
        resp['Content-Disposition'] = f'inline; filename="receipt-{order.order_number}-{fmt}.pdf"'
        return resp


class ShippingDocumentPDFAPIView(_APIView):
    authentication_classes = [_SessionAuth]
    permission_classes = [_IsAuth, _HasPerms]
    required_permissions = ('sale.view_salesorder',)
    http_method_names = ['get', 'head', 'options']

    def get(self, request, kind, pk):
        from django.conf import settings as _settings
        from django.shortcuts import get_object_or_404
        from django.utils import timezone as _tz
        from common.io_utils import render_to_string
        from report.rendering import ReportUnavailable, asset_fetcher, pdf_engine
        from sale.models import Challan, Delivery, PackingSlip

        if kind == 'delivery':
            obj = get_object_or_404(Delivery.objects.prefetch_related('items__sales_order_item__product'), pk=pk)
            headers = ['Product', 'Qty']
            rows = [[it.sales_order_item.product.name, str(it.quantity)] for it in obj.items.all()]
            title = f'Delivery {obj.reference_number}'
        elif kind == 'packing-slip':
            obj = get_object_or_404(PackingSlip.objects.prefetch_related('items__sales_order_item__product'), pk=pk)
            headers = ['Product', 'Qty']
            rows = [[it.sales_order_item.product.name, str(it.quantity)] for it in obj.items.all()]
            title = f'Packing Slip {obj.reference_number}'
        elif kind == 'challan':
            obj = get_object_or_404(Challan.objects.select_related('courier').prefetch_related('items__packing_slip'), pk=pk)
            headers = ['Packing Slip', 'Sales Order']
            rows = [[it.packing_slip.reference_number if it.packing_slip else '-', it.sales_order_id or '-'] for it in obj.items.all()]
            title = f'Challan {obj.reference_number}'
        else:
            from rest_framework.exceptions import NotFound
            raise NotFound('Unknown shipping document kind.')

        company = {'name': 'eManager', 'address': '', 'phone': '', 'email': '', 'logo_path': ''}
        company.update(getattr(_settings, 'REPORT_COMPANY', {}))
        try:
            CSS, HTML, FontConfiguration = pdf_engine()
            fonts = FontConfiguration()
            html = render_to_string('common/export_table.html', {
                'report': {
                    'company': company, 'title': title, 'headers': headers, 'rows': rows,
                    'generated_at': _tz.localtime().strftime('%Y-%m-%d %H:%M'),
                },
                'is_print': False,
            })
            pdf = HTML(string=html, base_url='report-asset:///', url_fetcher=asset_fetcher(company)).write_pdf(font_config=fonts)
        except (ImportError, OSError, ValueError, RuntimeError):
            raise ReportUnavailable() from None
        resp = HttpResponse(pdf, content_type='application/pdf')
        resp['Content-Disposition'] = f'inline; filename="{kind}-{pk}.pdf"'
        return resp


class AnalyticsReportAPIView(_APIView):
    authentication_classes = [_SessionAuth]
    permission_classes = [_IsAuth]
    content_negotiation_class = _RawPDFNegotiation
    http_method_names = ['get', 'head', 'options']

    def get(self, request, slug):
        from datetime import date
        from io import StringIO
        import csv as _csv
        from django.conf import settings as _settings
        from django.template.loader import render_to_string
        from django.utils import timezone as _tz
        from rest_framework.renderers import JSONRenderer as _JSONRenderer
        from common.io_utils import build_xlsx_bytes
        from report.analytics import ReportFilter, run_report
        from report.rendering import ReportUnavailable, asset_fetcher, pdf_engine

        qp = request.query_params
        flt = ReportFilter(
            date_from=date.fromisoformat(qp['date_from']) if qp.get('date_from') else None,
            date_to=date.fromisoformat(qp['date_to']) if qp.get('date_to') else None,
            warehouse_id=int(qp['warehouse_id']) if qp.get('warehouse_id') else None,
            customer_id=int(qp['customer_id']) if qp.get('customer_id') else None,
            supplier_id=int(qp['supplier_id']) if qp.get('supplier_id') else None,
            biller_id=int(qp['biller_id']) if qp.get('biller_id') else None,
            user_id=int(qp['user_id']) if qp.get('user_id') else None,
            status=qp.get('status') or None,
            as_of=date.fromisoformat(qp['as_of']) if qp.get('as_of') else None,
            page=int(qp.get('page') or 1),
            page_size=int(qp.get('page_size') or 50),
            basis=qp.get('basis') or 'created',
        )
        fmt = (qp.get('format') or 'json').lower()
        if fmt in ('csv', 'xlsx', 'pdf', 'print'):
            flt.page_size = 0  # full export
        res = run_report(slug, flt, request.user)
        headers = [c['label'] for c in res.columns]
        keys = [c['key'] for c in res.columns]
        rows = [[str(r.get(k, '')) for k in keys] for r in res.rows]

        if fmt == 'csv':
            buf = StringIO()
            _csv.writer(buf).writerows([headers, *rows])
            resp = HttpResponse(buf.getvalue(), content_type='text/csv; charset=utf-8')
            resp['Content-Disposition'] = f'attachment; filename="{slug}.csv"'
            return resp
        if fmt == 'xlsx':
            data = build_xlsx_bytes(slug, headers, rows)
            resp = HttpResponse(data, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            resp['Content-Disposition'] = f'attachment; filename="{slug}.xlsx"'
            return resp
        if fmt in ('pdf', 'print'):
            company = {'name': 'eManager', 'address': '', 'phone': '', 'email': '', 'logo_path': ''}
            company.update(getattr(_settings, 'REPORT_COMPANY', {}))
            html = render_to_string('common/export_table.html', {
                'report': {
                    'company': company, 'title': f'{slug} Report', 'headers': headers, 'rows': rows,
                    'generated_at': _tz.localtime().strftime('%Y-%m-%d %H:%M'),
                },
                'is_print': fmt == 'print',
            })
            if fmt == 'print':
                return HttpResponse(html, content_type='text/html; charset=utf-8')
            try:
                CSS, HTML, FontConfiguration = pdf_engine()
                pdf = HTML(string=html, base_url='report-asset:///', url_fetcher=asset_fetcher(company)).write_pdf(font_config=FontConfiguration())
            except (ImportError, OSError, ValueError, RuntimeError):
                raise ReportUnavailable() from None
            resp = HttpResponse(pdf, content_type='application/pdf')
            resp['Content-Disposition'] = f'inline; filename="{slug}.pdf"'
            return resp

        from rest_framework.response import Response as _Resp
        request.accepted_renderer = _JSONRenderer()
        return _Resp(res.to_dict())


class DashboardAPIView(_APIView):
    authentication_classes = [_SessionAuth]
    permission_classes = [_IsAuth]
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        from datetime import date
        from rest_framework.response import Response as _Resp
        from report.analytics import build_dashboard_payload

        qp = request.query_params
        payload = build_dashboard_payload(
            request.user,
            warehouse_id=int(qp['warehouse_id']) if qp.get('warehouse_id') else None,
            preset=qp.get('preset') or 'month',
            start_date=date.fromisoformat(qp['start_date']) if qp.get('start_date') else None,
            end_date=date.fromisoformat(qp['end_date']) if qp.get('end_date') else None,
        )
        return _Resp(payload)
