from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.utils.functional import cached_property
from django.views.decorators.cache import never_cache
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from account.permissions import has_role_permissions
from common.view import BaseAPIMixin
from .models import StockCount
from .scope import WarehouseScopeAPIMixin
from .stock_count_files import CountEntry, decimal_text, parse_count_file, count_sheet
from .stock_counts import (
    ADD_COUNT, FINALIZE_COUNT, VIEW_COUNT, count_options, count_records, create_stock_count, finalize_stock_count,
    summarize_count,
)


def count_page(scope, params):
    records = count_records(scope, query=params.get('q', '').strip(), ordering=params.get('ordering', '-created_at'))
    size = params.get('page_size', '10')
    if size == 'all':
        rows = list(records)
        return Paginator(rows, max(len(rows), 1)).page(1), size
    if size not in ('10', '25', '50'):
        size = '10'
    return Paginator(records, int(size)).get_page(params.get('page', 1)), size


class StockCountCreateSerializer(serializers.Serializer):
    warehouse = serializers.IntegerField(min_value=1, max_value=9223372036854775807, source='warehouse_id')
    count_type = serializers.ChoiceField(choices=StockCount.Type.choices)
    category = serializers.IntegerField(min_value=1, max_value=9223372036854775807, required=False, allow_null=True,
                                        source='category_id')
    brand = serializers.IntegerField(min_value=1, max_value=9223372036854775807, required=False, allow_null=True,
                                     source='brand_id')
    blind_count = serializers.BooleanField(default=False)


class StockCountSerializer(serializers.ModelSerializer):
    count_type_label = serializers.CharField(source='get_count_type_display')
    status_label = serializers.CharField(source='get_status_display')
    adjustment_number = serializers.SerializerMethodField()

    class Meta:
        model = StockCount
        fields = (
            'id', 'reference_no', 'warehouse', 'warehouse_name', 'count_type', 'count_type_label',
            'category', 'category_name', 'brand', 'brand_name', 'blind_count', 'total_items',
            'status', 'status_label', 'create_by', 'created_at', 'finalized_by', 'finalized_at', 'adjustment_number',
        )

    @cached_property
    def can_view_adjustment(self):
        return has_role_permissions(self.context['request'].user, ('warehouse.view_stockadjustment',))

    def get_adjustment_number(self, count):
        if count.adjustment_id and self.can_view_adjustment:
            return count.adjustment.adjustment_number
        return None


class InlineCountSerializer(serializers.Serializer):
    sku = serializers.CharField(max_length=64)
    counted_qty = serializers.DecimalField(max_digits=18, decimal_places=6, min_value=0)
    expected_qty = serializers.DecimalField(max_digits=18, decimal_places=6, min_value=0, required=False)


class StockCountFinalizeSerializer(serializers.Serializer):
    file = serializers.FileField(required=False)
    items = InlineCountSerializer(many=True, required=False, allow_empty=False)
    post_adjustment = serializers.BooleanField(default=False)

    def validate(self, attrs):
        if ('file' in attrs) == ('items' in attrs):
            raise serializers.ValidationError('Upload a CSV or submit inline counts. Choose one input method.')
        if 'file' in attrs:
            attrs['entries'] = parse_count_file(attrs['file'])
        else:
            attrs['entries'] = [
                CountEntry(number, row['sku'], row['counted_qty'], row.get('expected_qty'))
                for number, row in enumerate(attrs['items'], start=1)
            ]
        return attrs


@method_decorator(never_cache, name='dispatch')
class StockCountAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, GenericAPIView):
    queryset = StockCount.objects.all()
    serializer_class = StockCountSerializer
    required_permissions = VIEW_COUNT
    http_method_names = ['get', 'head', 'options']

    def get_count(self, pk, *, finalized=False):
        counts = self.get_queryset().select_related('adjustment')
        if finalized:
            counts = counts.filter(status=StockCount.Status.FINALIZED)
        return get_object_or_404(counts, pk=pk)

    def header(self, count):
        return self.get_serializer(count).data


class StockCountListAPIView(StockCountAPIView):
    required_permissions = {'GET': VIEW_COUNT, 'POST': ADD_COUNT}
    http_method_names = ['get', 'head', 'post', 'options']

    def get(self, request):
        page, size = count_page(request.warehouse_scope, request.query_params)
        return Response({
            'count': page.paginator.count, 'page': page.number, 'page_size': size,
            'next': self.page_link(page.next_page_number()) if page.has_next() else None,
            'previous': self.page_link(page.previous_page_number()) if page.has_previous() else None,
            'results': self.get_serializer(page.object_list, many=True).data,
        })

    def page_link(self, number):
        query = self.request.query_params.copy()
        query['page'] = number
        return self.request.build_absolute_uri(f'?{query.urlencode()}')

    def post(self, request):
        serializer = StockCountCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        count = create_stock_count(request.user, **serializer.validated_data)
        return Response({'message': 'Stock count created.', 'stock_count': self.header(count)}, status=201)


class StockCountOptionsAPIView(StockCountAPIView):
    required_permissions = ADD_COUNT

    def get(self, request):
        return Response({
            name: [{'value': row.pk, 'label': str(row)} for row in rows]
            for name, rows in count_options(request.warehouse_scope).items()
        })


class StockCountDetailAPIView(StockCountAPIView):
    finalized = False

    def get(self, request, pk):
        count = self.get_count(pk, finalized=self.finalized)
        complete = count.status == StockCount.Status.FINALIZED
        items = list(count.items.all())
        rows = []
        for item in items:
            row = {
                'sku': item.sku, 'product_name': item.product_name, 'unit': item.unit_code,
                'expected_qty': None if count.blind_count and not complete else decimal_text(item.expected_qty),
            }
            if complete:
                row.update(
                    counted_qty=decimal_text(item.counted_qty), variance_qty=decimal_text(item.variance_qty),
                    unit_cost=decimal_text(item.unit_cost), currency=item.currency_code,
                    variance_value=decimal_text(item.variance_value), stock_moved=item.stock_moved,
                )
            rows.append(row)
        data = {**self.header(count), 'items': rows}
        if complete:
            summary = summarize_count(items)
            for totals in (*summary['quantities'], *summary['values']):
                for name, value in totals.items():
                    if name not in ('unit', 'currency'):
                        totals[name] = decimal_text(value)
            data['summary'] = summary
        return Response(data)


class StockCountReportAPIView(StockCountDetailAPIView):
    finalized = True


class StockCountFinalizeAPIView(StockCountAPIView):
    http_method_names = ['post', 'options']
    required_permissions = FINALIZE_COUNT

    def post(self, request, pk):
        count = self.get_count(pk)
        serializer = StockCountFinalizeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        count, finalized_now = finalize_stock_count(request.user, count.pk, data['entries'],
                                                    post_adjustment=data['post_adjustment'])
        return Response({
            'message': 'Stock count finalized.' if finalized_now else 'This stock count is already finalized.',
            'finalized_now': finalized_now, 'stock_count': self.header(count),
        })


class StockCountFileAPIView(StockCountAPIView):
    final = False

    def get(self, request, pk):
        return count_sheet(self.get_count(pk, finalized=self.final), final=self.final)
