from common.models import ProductStatus
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.shortcuts import get_object_or_404
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response

from account.permissions import HasRequiredPermissions
from common.io_utils import ExportListMixin
from common.view import BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView, BaseUpdateAPIView
from .models import (
    Brand, Currency, Product, ProductCategory, ProductComboItem,
    ProductType, TaxRule, UnitOfMeasure, WarehouseProductPrice,
)
from .pricing import resolve_unit_price
from .serializers import (
    BrandSerializer, CurrencySerializer, ProductCategorySerializer, ProductComboItemSerializer,
    ProductDetailSerializer, ProductSerializer, ProductTypeSerializer, StatusSerializer,
    TaxRuleSerializer, UnitOfMeasureSerializer, WarehouseProductPriceSerializer,
)


@method_decorator(never_cache, name='dispatch')
class ProductOptionsAPIView(APIView):
    """Minimal reference data for product creation, without catalog management grants."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated, HasRequiredPermissions]
    required_permissions = ('inventory.add_product',)
    http_method_names = ['get', 'head', 'options']

    def get_base_products(self):
        return Product.objects.filter(variant_of__isnull=True)

    def get(self, request):
        def choices(model, value_field='pk', include_code=False):
            fields = [value_field, 'name'] + (['code'] if include_code and value_field != 'code' else [])
            return [{'value': row[value_field],
                     'label': f"{row['name']} ({row['code']})" if include_code else row['name']}
                    for row in model.objects.order_by('name', 'pk').values(*fields)]

        return Response({
            'product_type': choices(ProductType, 'code'),
            'status': choices(ProductStatus, 'code'),
            'category': choices(ProductCategory),
            'brand': choices(Brand),
            'unit': choices(UnitOfMeasure, include_code=True),
            'currency': choices(Currency, 'code', include_code=True),
            'variant_of': [{'value': row['pk'], 'label': f"{row['sku']} — {row['name']}"}
                           for row in self.get_base_products()
                           .order_by('name', 'pk').values('pk', 'sku', 'name')],
        })


class ProductEditOptionsAPIView(ProductOptionsAPIView):
    required_permissions = ('inventory.view_product', 'inventory.change_product')

    def get(self, request, pk):
        self.product = get_object_or_404(Product, pk=pk)
        return super().get(request)

    def get_base_products(self):
        if self.product.variants.exists():
            return Product.objects.none()
        return super().get_base_products().exclude(pk=self.product.pk)


class ProductListCreateAPIView(ExportListMixin, BaseListCreateAPIView):
    serializer_class = ProductSerializer
    queryset = Product.objects.select_related('product_type', 'status', 'category', 'brand', 'unit', 'variant_of', 'currency')
    response_key = 'product'
    search_fields = ['name', 'sku', 'barcode', 'manufacturer', 'manufacturer_part_number']
    export_filename = 'products'
    export_title = 'Products'
    export_columns = [
        ('SKU', 'sku'), ('Name', 'name'), ('Barcode', 'barcode'), ('Type', 'product_type_id'),
        ('Category', 'category.name'), ('Brand', 'brand.name'), ('Unit', 'unit.code'),
        ('Cost Price', 'cost_price'), ('Sale Price', 'sale_price'),
    ]


class ProductDetailAPIView(BaseDetailAPIView):
    serializer_class = ProductDetailSerializer
    queryset = Product.objects.select_related('product_type', 'status', 'category', 'brand', 'unit', 'variant_of', 'currency')


class ProductCategoryListCreateAPIView(BaseListCreateAPIView):
    serializer_class = ProductCategorySerializer
    queryset = ProductCategory.objects.all()
    response_key = 'category'
    search_fields = ['name', 'code']


class ProductCategoryDetailAPIView(BaseDetailAPIView):
    serializer_class = ProductCategorySerializer
    queryset = ProductCategory.objects.all()


class BrandListCreateAPIView(BaseListCreateAPIView):
    serializer_class = BrandSerializer
    queryset = Brand.objects.all()
    response_key = 'brand'


class BrandDetailAPIView(BaseDetailAPIView):
    serializer_class = BrandSerializer
    queryset = Brand.objects.all()


class UnitOfMeasureListCreateAPIView(BaseListCreateAPIView):
    serializer_class = UnitOfMeasureSerializer
    queryset = UnitOfMeasure.objects.all()
    response_key = 'unit'
    search_fields = ['name', 'code']


class UnitOfMeasureDetailAPIView(BaseDetailAPIView):
    serializer_class = UnitOfMeasureSerializer
    queryset = UnitOfMeasure.objects.all()


class ProductTypeListCreateAPIView(BaseListCreateAPIView):
    serializer_class = ProductTypeSerializer
    queryset = ProductType.objects.all()
    response_key = 'product_type'
    search_fields = ['name', 'code']


class ProductTypeDetailAPIView(BaseDetailAPIView):
    serializer_class = ProductTypeSerializer
    queryset = ProductType.objects.all()
    protected_codes = ('goods',)


class StatusListCreateAPIView(BaseListCreateAPIView):
    serializer_class = StatusSerializer
    queryset = ProductStatus.objects.all()
    response_key = 'status'
    search_fields = ['name', 'code']


class StatusDetailAPIView(BaseDetailAPIView):
    serializer_class = StatusSerializer
    queryset = ProductStatus.objects.all()
    protected_codes = ('draft',)


class ProductListAPIView(BaseListAPIView):
    serializer_class = ProductSerializer
    queryset = Product.objects.select_related('product_type', 'status', 'category', 'brand', 'unit', 'variant_of', 'currency')
    search_fields = ['name', 'sku', 'barcode', 'manufacturer', 'manufacturer_part_number']


class ProductCategoryListAPIView(BaseListAPIView):
    serializer_class = ProductCategorySerializer
    queryset = ProductCategory.objects.all()
    search_fields = ['name', 'code']


class BrandListAPIView(BaseListAPIView):
    serializer_class = BrandSerializer
    queryset = Brand.objects.all()


class UnitOfMeasureListAPIView(BaseListAPIView):
    serializer_class = UnitOfMeasureSerializer
    queryset = UnitOfMeasure.objects.all()
    search_fields = ['name', 'code']


class ProductTypeListAPIView(BaseListAPIView):
    serializer_class = ProductTypeSerializer
    queryset = ProductType.objects.all()
    search_fields = ['name', 'code']


class StatusListAPIView(BaseListAPIView):
    serializer_class = StatusSerializer
    queryset = ProductStatus.objects.all()
    search_fields = ['name', 'code']


class CurrencyListCreateAPIView(BaseListCreateAPIView):
    serializer_class = CurrencySerializer
    queryset = Currency.objects.all()
    response_key = 'currency'
    search_fields = ['name', 'code', 'symbol']


class CurrencyListAPIView(BaseListAPIView):
    serializer_class = CurrencySerializer
    queryset = Currency.objects.all()
    search_fields = ['name', 'code', 'symbol']


class CurrencyDetailAPIView(BaseDetailAPIView):
    serializer_class = CurrencySerializer
    queryset = Currency.objects.all()


class ProductUpdateAPIView(BaseUpdateAPIView):
    serializer_class = ProductSerializer
    queryset = ProductDetailAPIView.queryset


class ProductCategoryUpdateAPIView(BaseUpdateAPIView):
    serializer_class = ProductCategorySerializer
    queryset = ProductCategoryDetailAPIView.queryset


class BrandUpdateAPIView(BaseUpdateAPIView):
    serializer_class = BrandSerializer
    queryset = BrandDetailAPIView.queryset


class UnitOfMeasureUpdateAPIView(BaseUpdateAPIView):
    serializer_class = UnitOfMeasureSerializer
    queryset = UnitOfMeasureDetailAPIView.queryset


class ProductTypeUpdateAPIView(BaseUpdateAPIView):
    serializer_class = ProductTypeSerializer
    queryset = ProductTypeDetailAPIView.queryset


class TaxRuleListCreateAPIView(BaseListCreateAPIView):
    serializer_class = TaxRuleSerializer
    queryset = TaxRule.objects.all()
    response_key = 'tax_rule'
    search_fields = ['name']


class TaxRuleListAPIView(BaseListAPIView):
    serializer_class = TaxRuleSerializer
    queryset = TaxRule.objects.all()
    search_fields = ['name']


class TaxRuleDetailAPIView(BaseDetailAPIView):
    serializer_class = TaxRuleSerializer
    queryset = TaxRule.objects.all()


class TaxRuleUpdateAPIView(BaseUpdateAPIView):
    serializer_class = TaxRuleSerializer
    queryset = TaxRule.objects.all()


class ProductComboItemListCreateAPIView(BaseListCreateAPIView):
    serializer_class = ProductComboItemSerializer
    queryset = ProductComboItem.objects.select_related('combo_product', 'component_product')
    response_key = 'combo_item'
    ordering = ['pk']
    search_fields = ['component_product__sku', 'component_product__name']

    def get_queryset(self):
        return super().get_queryset().filter(combo_product_id=self.kwargs['product_id'])

    def perform_create(self, serializer):
        combo = get_object_or_404(Product, pk=self.kwargs['product_id'])
        serializer.save(
            combo_product=combo,
            create_by=self.request.user.get_username(),
            modified_by=self.request.user.get_username(),
        )


class ProductComboItemDetailAPIView(BaseDetailAPIView):
    serializer_class = ProductComboItemSerializer
    queryset = ProductComboItem.objects.select_related('combo_product', 'component_product')

    def get_queryset(self):
        return super().get_queryset().filter(combo_product_id=self.kwargs['product_id'])


class WarehouseProductPriceListCreateAPIView(BaseListCreateAPIView):
    serializer_class = WarehouseProductPriceSerializer
    queryset = WarehouseProductPrice.objects.select_related('warehouse', 'product')
    response_key = 'warehouse_price'
    ordering = ['-effective_from', 'pk']
    search_fields = ['warehouse__name']

    def get_queryset(self):
        return super().get_queryset().filter(product_id=self.kwargs['product_id'])

    def perform_create(self, serializer):
        product = get_object_or_404(Product, pk=self.kwargs['product_id'])
        serializer.save(
            product=product,
            create_by=self.request.user.get_username(),
            modified_by=self.request.user.get_username(),
        )


@method_decorator(never_cache, name='dispatch')
class ResolveProductPriceAPIView(BaseAPIMixin, APIView):
    required_permissions = ('inventory.view_product',)
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        from decimal import Decimal
        from django.utils import timezone
        from people.models import Party

        product = get_object_or_404(Product, pk=request.query_params.get('product'))
        wh_id = request.query_params.get('warehouse') or None
        cust_id = request.query_params.get('customer') or None
        customer = Party.objects.select_related('customer_group').filter(pk=cust_id).first() if cust_id else None
        raw_date = request.query_params.get('date')
        doc_date = timezone.datetime.fromisoformat(raw_date).date() if raw_date else timezone.localdate()
        qty = Decimal(request.query_params.get('quantity') or '1')
        manual = request.query_params.get('manual_price')
        resolved = resolve_unit_price(
            product, warehouse=int(wh_id) if wh_id else None, customer=customer,
            date=doc_date, quantity=qty, manual_price=Decimal(manual) if manual is not None else None,
        )
        return Response({
            'price': str(resolved.price),
            'source': resolved.source,
            'rule_id': resolved.rule_id,
            'tax_rule': resolved.tax_rule.pk if resolved.tax_rule else None,
            'tax_rate': str(resolved.tax_rate),
            'tax_method': resolved.tax_method,
            'wholesale_price': str(resolved.wholesale_price) if resolved.wholesale_price is not None else None,
        })


class StatusUpdateAPIView(BaseUpdateAPIView):
    serializer_class = StatusSerializer
    queryset = StatusDetailAPIView.queryset


class CurrencyUpdateAPIView(BaseUpdateAPIView):
    serializer_class = CurrencySerializer
    queryset = CurrencyDetailAPIView.queryset
