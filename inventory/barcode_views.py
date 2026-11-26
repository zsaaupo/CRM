from decimal import Decimal
from io import BytesIO

from django.db.models import Case, Q, Value, When
from django.http import FileResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import serializers
from rest_framework.parsers import JSONParser
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from account.page_views import ProtectedAccountPageView
from common.view import BaseAPIMixin
from report.rendering import render_pdf
from report.views import PDFRenderer
from .barcodes import CUSTOM_DIMENSIONS, LABEL_FIELDS, MAX_LABELS, PRESETS, custom_layout, print_document, retail_barcode
from .models import Product


class PrintBarcodesPageView(ProtectedAccountPageView):
    template_name = 'inventory/print_barcodes.html'
    permission_required = ('inventory.view_product',)

    def get_context_data(self, **kwargs):
        return {
            **super().get_context_data(**kwargs),
            'page_section': 'inventory',
            'inventory_mode': 'print-barcodes',
            'page_label': 'Print barcodes',
            'presets': [(key, name) for key, (name, _) in PRESETS.items()],
            'label_fields': LABEL_FIELDS,
            'custom_dimensions': CUSTOM_DIMENSIONS,
            'max_labels': MAX_LABELS,
            'search_url': reverse('inventory:barcode-search'),
            'print_url': reverse('inventory:print-barcodes'),
        }


class BarcodeLineSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1, max_value=2**63 - 1)
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_LABELS)


class LabelConfigSerializer(serializers.Serializer):
    auto_retail_barcodes = serializers.BooleanField(default=False)

    def get_fields(self):
        fields = super().get_fields()
        for key, _ in LABEL_FIELDS:
            fields[f'show_{key}'] = serializers.BooleanField(default=True)
            fields[f'{key}_size'] = serializers.IntegerField(min_value=6, max_value=36, default=15)
        return fields


class CustomPaperSerializer(serializers.Serializer):
    def get_fields(self):
        return {
            key: serializers.DecimalField(
                max_digits=7, decimal_places=3, max_value=Decimal('1000'),
                min_value=Decimal('0.001') if key.endswith(('width', 'height')) else Decimal(0),
            )
            for key, _, _ in CUSTOM_DIMENSIONS
        }


class PaperConfigSerializer(serializers.Serializer):
    preset = serializers.ChoiceField(choices=[*PRESETS, 'custom'])
    custom = CustomPaperSerializer(required=False, allow_null=True)

    def validate(self, attrs):
        dimensions = attrs.get('custom')
        if attrs['preset'] == 'custom':
            if dimensions is None:
                raise serializers.ValidationError({'custom': 'Enter the custom paper dimensions.'})
            try:
                attrs['layout'] = custom_layout(dimensions)
            except ValueError as exc:
                raise serializers.ValidationError({'custom': str(exc)}) from exc
        else:
            if dimensions is not None:
                raise serializers.ValidationError({'custom': 'Select Custom paper to use these dimensions.'})
            attrs['layout'] = PRESETS[attrs['preset']][1]
        return attrs


class PrintBarcodesSerializer(serializers.Serializer):
    items = BarcodeLineSerializer(many=True, allow_empty=False, max_length=MAX_LABELS)
    label_config = LabelConfigSerializer()
    paper_config = PaperConfigSerializer()

    def validate_items(self, items):
        if sum(item['quantity'] for item in items) > MAX_LABELS:
            raise serializers.ValidationError(f'Print up to {MAX_LABELS} labels per batch.')
        if len({item['product_id'] for item in items}) != len(items):
            raise serializers.ValidationError('Use one queue row per product.')
        return items


@method_decorator(never_cache, name='dispatch')
class BarcodeSearchAPIView(BaseAPIMixin, APIView):
    required_permissions = ('inventory.view_product',)
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        query = request.query_params.get('q', '').strip()
        exact = request.query_params.get('exact') == '1'
        if len(query) < (1 if exact else 2) or len(query) > 255:
            return Response([])
        identity = Q(barcode__iexact=query) | Q(sku__iexact=query)
        products = Product.objects.all()
        if exact:
            # Some scanners report UPC-A with the equivalent EAN-13 leading zero.
            if (len(query) == 12 or query.startswith('0')) and retail_barcode(query) is not None:
                alternate = '0' + query if len(query) == 12 else query[1:]
                identity |= Q(barcode=alternate) | Q(sku=alternate)
            products = products.filter(identity).order_by('name', 'pk')
        else:
            products = products.filter(
                Q(name__icontains=query) | Q(sku__icontains=query) | Q(barcode__icontains=query),
            ).order_by(Case(When(identity, then=Value(0)), default=Value(1)), 'name', 'pk')
        today = timezone.localdate()
        return Response([
            {
                'id': product.pk, 'name': product.name, 'sku': product.sku, 'code': product.label_code,
                'price': str(product.sale_price), 'currency': product.currency_id,
                'promo_price': str(product.promo_price) if product.promo_price is not None else None,
                'has_active_promo': product.has_active_promo(today),
            }
            for product in products[:20]
        ])


@method_decorator(never_cache, name='dispatch')
class PrintBarcodesAPIView(BaseAPIMixin, APIView):
    required_permissions = ('inventory.view_product',)
    http_method_names = ['post', 'options']
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer, PDFRenderer]

    def post(self, request):
        serializer = PrintBarcodesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = serializer.validated_data
        items = job['items']
        products = Product.objects.select_related('currency').in_bulk([item['product_id'] for item in items])
        if len(products) != len(items):
            raise serializers.ValidationError({'items': 'Remove products that are no longer in the catalog.'})
        try:
            document = print_document(items, products, job['label_config'], job['paper_config']['layout'])
        except ValueError as exc:
            raise serializers.ValidationError({'items': str(exc)}) from exc
        pdf = render_pdf(document, 'inventory/barcode_pdf.html', stylesheet=None)
        response = FileResponse(BytesIO(pdf), filename='barcode-labels.pdf', content_type='application/pdf')
        response['Cache-Control'] = 'private, no-store, max-age=0'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
