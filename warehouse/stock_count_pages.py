import json

from django.conf import settings
from django.contrib import messages
from django.core import signing
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.exceptions import APIException, ValidationError

from account.page_views import ProtectedAccountPageView
from account.permissions import has_role_permissions
from report.rendering import ReportUnavailable, render_pdf
from .models import StockCount
from .scope import WarehouseScopePageMixin, scoped_queryset, warehouse_scope
from .stock_count_api import StockCountCreateSerializer, StockCountFinalizeSerializer, count_page
from .stock_count_files import csv_response, csv_text, decimal_text, xlsx_response
from .stock_counts import (
    ADD_COUNT, COUNT_ORDERING, DRAFT_COUNT_ADJUSTMENT, FINALIZE_COUNT, VIEW_COUNT, count_options,
    create_finalized_count_adjustment, create_stock_count, finalize_stock_count, match_counts, summarize_count,
)


LIST_COLUMNS = (
    ('created_at', 'Date'), ('reference_no', 'Reference'), ('warehouse', 'Warehouse'),
    ('category', 'Category'), ('brand', 'Brand'), ('count_type', 'Type'),
    ('initial_file', 'Initial file'), ('final_file', 'Final file'), ('actions', 'Actions'),
)


def list_export(file_type, rows, columns):
    columns = [column for column in columns if column['visible'] and column['key'] != 'actions']
    headers = [column['label'] for column in columns]
    values = []
    for count in rows:
        data = {
            'created_at': timezone.localtime(count.created_at).strftime('%d-%m-%Y %H:%M:%S'),
            'reference_no': count.reference_no, 'warehouse': count.warehouse_name, 'category': count.category_name,
            'brand': count.brand_name, 'count_type': count.get_count_type_display(),
            'initial_file': count.total_items,
            'final_file': count.total_items if count.status == StockCount.Status.FINALIZED else '',
        }
        values.append([data[column['key']] for column in columns])
    if file_type == 'csv':
        return csv_response('stock-counts.csv', headers, ([csv_text(str(value)) for value in row] for row in values))
    if file_type == 'xlsx':
        return xlsx_response('stock-counts.xlsx', headers, values)
    company = {'name': 'eManager', 'logo_path': '', **getattr(settings, 'REPORT_COMPANY', {})}
    content = render_pdf({'company': company, 'headers': headers, 'rows': values,
                          'generated_at': timezone.localtime()}, 'warehouse/stock_count_list_pdf.html')
    return HttpResponse(content, content_type='application/pdf',
                        headers={'Content-Disposition': 'attachment; filename="stock-counts.pdf"'})


def form_errors(detail, path=()):
    if isinstance(detail, dict):
        return [error for key, value in detail.items() for error in form_errors(value, (*path, key))]
    if isinstance(detail, (list, tuple)):
        indexed = any(isinstance(value, dict) for value in detail)
        return [error for number, value in enumerate(detail, start=1)
                for error in form_errors(value, (*path, f'Row {number}') if indexed else path)]
    label = ' / '.join(str(part).replace('_', ' ') for part in path if part not in ('non_field_errors', 'items'))
    return [f'{label.capitalize()}: {detail}' if label else str(detail)]


@method_decorator(never_cache, name='dispatch')
class StockCountPageView(WarehouseScopePageMixin, ProtectedAccountPageView):
    permission_required = VIEW_COUNT
    list_url = reverse_lazy('warehouse_pages:stock-count-list')

    def get_context_data(self, **kwargs):
        return super().get_context_data(page_section='warehouses', page_mode='stock-count',
                                        page_label='Stock counts', list_url=self.list_url, **kwargs)

    def get_count(self, pk, *, finalized=False):
        scope = warehouse_scope(self.request.user)
        records = scoped_queryset(StockCount.objects.select_related('adjustment'), scope)
        if finalized:
            records = records.filter(status=StockCount.Status.FINALIZED)
        return get_object_or_404(records, pk=pk)


class StockCountListPageView(StockCountPageView):
    template_name = 'warehouse/stock_count_list.html'

    def get(self, request, *args, **kwargs):
        page, size = count_page(warehouse_scope(request.user), request.GET)
        ordering = request.GET.get('ordering', '-created_at')
        if ordering.removeprefix('-') not in COUNT_ORDERING:
            ordering = '-created_at'
        visible = set(request.GET.getlist('column'))
        columns = [{
            'key': key, 'label': label, 'sortable': key in COUNT_ORDERING,
            'sort': f'-{key}' if ordering == key else key,
            'aria_sort': 'ascending' if ordering == key else 'descending' if ordering == f'-{key}' else None,
            'visible': not visible or key in visible or key == 'reference_no',
        } for key, label in LIST_COLUMNS]
        context = self.get_context_data(
            page=page, page_size=size, ordering=ordering, columns=columns, search=request.GET.get('q', ''),
            can_create=has_role_permissions(request.user, ADD_COUNT),
            can_finalize=has_role_permissions(request.user, FINALIZE_COUNT),
        )
        file_type = request.GET.get('export')
        if file_type in ('csv', 'xlsx', 'pdf'):
            try:
                return list_export(file_type, page.object_list, columns)
            except ReportUnavailable as exc:
                context['errors'] = form_errors(exc.detail)
                return self.render_to_response(context, status=exc.status_code)
        return self.render_to_response(context)


class StockCountCreatePageView(StockCountPageView):
    template_name = 'warehouse/stock_count_create.html'
    permission_required = ADD_COUNT

    def get_context_data(self, **kwargs):
        return super().get_context_data(**count_options(warehouse_scope(self.request.user)), **kwargs)

    def post(self, request):
        submitted = request.POST
        serializer = StockCountCreateSerializer(data={
            'warehouse': submitted.get('warehouse'), 'count_type': submitted.get('count_type'),
            'category': submitted.get('category') or None, 'brand': submitted.get('brand') or None,
            'blind_count': submitted.get('blind_count') == 'on',
        })
        try:
            serializer.is_valid(raise_exception=True)
            create_stock_count(request.user, **serializer.validated_data)
        except APIException as exc:
            return self.render_to_response(self.get_context_data(values=submitted, errors=form_errors(exc.detail)),
                                           status=exc.status_code)
        return redirect('warehouse_pages:stock-count-list')


class StockCountFinalizePageView(StockCountPageView):
    template_name = 'warehouse/stock_count_finalize.html'
    permission_required = FINALIZE_COUNT

    def form_context(self, count, *, method='inline', entries=(), errors=()):
        by_sku = {row['sku']: row.get('counted_qty', '') for row in entries
                  if isinstance(row, dict) and isinstance(row.get('sku'), str)}
        return self.get_context_data(
            count=count, rows=[(item, by_sku.get(item.sku, '')) for item in count.items.all()],
            method=method, errors=errors,
        )

    def review_response(self, count, differences, *, token=None, errors=(), status=200):
        context = self.get_context_data(
            count=count, differences=differences, review_token=token, errors=errors,
            can_create_adjustment=has_role_permissions(self.request.user, DRAFT_COUNT_ADJUSTMENT),
        )
        return self.render_to_response(context, status=status)

    def get(self, request, pk):
        count = self.get_count(pk)
        if count.status == StockCount.Status.FINALIZED:
            return redirect('warehouse_pages:stock-count-report', pk=pk)
        return self.render_to_response(self.form_context(count))

    def post(self, request, pk):
        count = self.get_count(pk)
        action = request.POST.get('adjustment_action')
        if action == 'cancel' and count.status == StockCount.Status.FINALIZED:
            return redirect(self.list_url)
        if count.status == StockCount.Status.FINALIZED:
            return redirect('warehouse_pages:stock-count-report', pk=pk)

        method = request.POST.get('input_method', 'inline')
        token = request.POST.get('adjustment_review')
        salt = f'warehouse.stock-count-adjustment.{request.user.pk}.{count.pk}'
        items = list(count.items.all())
        entries = []
        differences = []
        try:
            if action is not None or token is not None:
                try:
                    if action not in ('create', 'cancel') or not token:
                        raise signing.BadSignature
                    quantities = signing.loads(token, salt=salt)
                    if len(quantities) != len(items):
                        raise signing.BadSignature
                except signing.BadSignature as exc:
                    raise ValidationError('Submit the counts again to review the adjustment.') from exc
                entries = [{'sku': item.sku, 'counted_qty': quantity} for item, quantity in zip(items, quantities)]
                payload = {'items': entries}
            elif method == 'csv':
                payload = {'file': request.FILES.get('file')}
            else:
                encoded = request.POST.get('inline_counts')
                if encoded:
                    try:
                        entries = json.loads(encoded)
                    except (ValueError, RecursionError):
                        entries = None
                else:
                    entries = [{'sku': item.sku, 'counted_qty': request.POST.get(f'count_{item.pk}', '')}
                               for item in items]
                payload = {'items': entries}

            serializer = StockCountFinalizeSerializer(data=payload)
            serializer.is_valid(raise_exception=True)
            validated = serializer.validated_data['entries']
            match_counts(items, validated)
            differences = [item for item in items if item.variance_qty]
            if differences and not action and not count.blind_count:
                review = [decimal_text(item.counted_qty) for item in items]
                token = signing.dumps(review, salt=salt, compress=True)
                return self.review_response(count, differences, token=token)

            count, finalized_now = finalize_stock_count(request.user, pk, validated, create_adjustment=action == 'create')
        except APIException as exc:
            errors = form_errors(exc.detail)
            if differences and token:
                return self.review_response(count, differences, token=token, errors=errors, status=exc.status_code)
            context = self.form_context(count, method=method, entries=entries if isinstance(entries, list) else (),
                                        errors=errors)
            return self.render_to_response(context, status=exc.status_code)

        if action == 'cancel':
            return redirect(self.list_url)
        if finalized_now and action == 'create' and count.adjustment_id:
            messages.success(request, 'Draft stock adjustment created.')
        # A blind count reveals variances only after the submitted quantities are locked.
        if finalized_now and count.blind_count and differences and not action:
            return self.review_response(count, differences)
        return redirect('warehouse_pages:stock-count-report', pk=pk)


class StockCountReportPageView(StockCountPageView):
    template_name = 'warehouse/stock_count_report.html'

    def get_context_data(self, **kwargs):
        count = self.get_count(self.kwargs['pk'], finalized=True)
        items = list(count.items.all())
        summary = summarize_count(items)
        return super().get_context_data(
            count=count, items=items, summary=summary,
            can_create_adjustment=(
                not count.adjustment_id and (summary['overage_items'] or summary['shortage_items'])
                and has_role_permissions(self.request.user, DRAFT_COUNT_ADJUSTMENT)
            ),
            can_view_adjustment=has_role_permissions(self.request.user, ('warehouse.view_stockadjustment',)), **kwargs,
        )

    def post(self, request, pk):
        try:
            created = create_finalized_count_adjustment(request.user, pk)
        except APIException as exc:
            return self.render_to_response(self.get_context_data(errors=form_errors(exc.detail)), status=exc.status_code)
        if created:
            messages.success(request, 'Draft stock adjustment created.')
        return redirect('warehouse_pages:stock-count-report', pk=pk)
