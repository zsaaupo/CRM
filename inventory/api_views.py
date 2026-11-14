from common.models import Status
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.shortcuts import get_object_or_404
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response

from account.permissions import HasRequiredPermissions
from common.view import BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView, BaseUpdateAPIView
from .models import Brand, Currency, Product, ProductCategory, ProductType, UnitOfMeasure
from .serializers import (
    BrandSerializer, CurrencySerializer, ProductCategorySerializer, ProductSerializer,
    ProductDetailSerializer, ProductTypeSerializer, StatusSerializer, UnitOfMeasureSerializer,
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
            'status': choices(Status, 'code'),
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


class ProductListCreateAPIView(BaseListCreateAPIView):
    serializer_class = ProductSerializer
    queryset = Product.objects.select_related('product_type', 'status', 'category', 'brand', 'unit', 'variant_of', 'currency')
    response_key = 'product'
    search_fields = ['name', 'sku', 'barcode', 'manufacturer', 'manufacturer_part_number']


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
    queryset = Status.objects.all()
    response_key = 'status'
    search_fields = ['name', 'code']


class StatusDetailAPIView(BaseDetailAPIView):
    serializer_class = StatusSerializer
    queryset = Status.objects.all()
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
    queryset = Status.objects.all()
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


class StatusUpdateAPIView(BaseUpdateAPIView):
    serializer_class = StatusSerializer
    queryset = StatusDetailAPIView.queryset


class CurrencyUpdateAPIView(BaseUpdateAPIView):
    serializer_class = CurrencySerializer
    queryset = CurrencyDetailAPIView.queryset
