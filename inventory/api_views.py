from django.db import IntegrityError, transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.generics import ListAPIView, ListCreateAPIView, RetrieveUpdateDestroyAPIView, UpdateAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response

from account.permissions import HasRequiredPermissions
from .models import Brand, Currency, Product, ProductCategory, ProductType, Status, UnitOfMeasure
from .serializers import (
    BrandSerializer, CurrencySerializer, ProductCategorySerializer, ProductSerializer,
    ProductTypeSerializer, StatusSerializer, UnitOfMeasureSerializer,
)


class InventoryPagination(PageNumberPagination):
    page_size = 20


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


class RecordInUse(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = 'This record is in use and cannot be deleted.'
    default_code = 'record_in_use'


class InventoryAPIMixin:
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated, HasRequiredPermissions]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    @property
    def required_permissions(self):
        """Keep AddUserAPIView's permission classes, using each inventory model."""
        opts = self.queryset.model._meta
        return {
            method: (f'{opts.app_label}.{action}_{opts.model_name}',)
            for method, action in [('GET', 'view'), ('POST', 'add'),
                                   ('PUT', 'change'), ('PATCH', 'change'), ('DELETE', 'delete')]
        }

    def save_record(self, serializer, **audit):
        try:
            with transaction.atomic():
                serializer.save(**audit)
        except IntegrityError as exc:
            raise ValidationError({
                'non_field_errors': ['Could not save. Check unique fields and related records, then try again.'],
            }) from exc


class InventoryListQuerysetMixin:
    pagination_class = InventoryPagination
    search_fields = ['name']

    def get_queryset(self):
        queryset = super().get_queryset()
        query = self.request.query_params.get('q', '').strip()
        if query:
            condition = Q()
            for field in self.search_fields:
                condition |= Q(**{f'{field}__icontains': query})
            queryset = queryset.filter(condition)
        return queryset.order_by('name', 'pk')


@method_decorator(never_cache, name='dispatch')
class InventoryListAPIView(InventoryAPIMixin, InventoryListQuerysetMixin, ListAPIView):
    http_method_names = ['get', 'head', 'options']


@method_decorator(never_cache, name='dispatch')
class InventoryListCreateAPIView(InventoryAPIMixin, InventoryListQuerysetMixin, ListCreateAPIView):
    http_method_names = ['get', 'post', 'head', 'options']

    def perform_create(self, serializer):
        actor = self.request.user.get_username()
        self.save_record(serializer, create_by=actor, modified_by=actor)

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        response.data = {
            'message': f'{self.queryset.model._meta.verbose_name.capitalize()} created successfully.',
            self.response_key: response.data,
        }
        return response


class InventoryUpdateMixin:
    def perform_update(self, serializer):
        self.save_record(serializer, modified_by=self.request.user.get_username())


@method_decorator(never_cache, name='dispatch')
class InventoryUpdateAPIView(InventoryUpdateMixin, InventoryAPIMixin, UpdateAPIView):
    http_method_names = ['put', 'patch', 'options']

    @property
    def required_permissions(self):
        opts = self.queryset.model._meta
        return (f'{opts.app_label}.change_{opts.model_name}',)


@method_decorator(never_cache, name='dispatch')
class InventoryDetailAPIView(InventoryUpdateMixin, InventoryAPIMixin, RetrieveUpdateDestroyAPIView):
    http_method_names = ['get', 'put', 'patch', 'delete', 'head', 'options']
    protected_codes = ()

    def perform_destroy(self, instance):
        if getattr(instance, 'code', None) in self.protected_codes:
            raise RecordInUse('The default lookup record cannot be deleted.')
        try:
            with transaction.atomic():
                instance.delete()
        except ProtectedError as exc:
            raise RecordInUse() from exc


class ProductListCreateAPIView(InventoryListCreateAPIView):
    serializer_class = ProductSerializer
    queryset = Product.objects.select_related('product_type', 'status', 'category', 'brand', 'unit', 'variant_of', 'currency')
    response_key = 'product'
    search_fields = ['name', 'sku', 'barcode', 'manufacturer', 'manufacturer_part_number']


class ProductDetailAPIView(InventoryDetailAPIView):
    serializer_class = ProductSerializer
    queryset = Product.objects.select_related('product_type', 'status', 'category', 'brand', 'unit', 'variant_of', 'currency')


class ProductCategoryListCreateAPIView(InventoryListCreateAPIView):
    serializer_class = ProductCategorySerializer
    queryset = ProductCategory.objects.all()
    response_key = 'category'
    search_fields = ['name', 'code']


class ProductCategoryDetailAPIView(InventoryDetailAPIView):
    serializer_class = ProductCategorySerializer
    queryset = ProductCategory.objects.all()


class BrandListCreateAPIView(InventoryListCreateAPIView):
    serializer_class = BrandSerializer
    queryset = Brand.objects.all()
    response_key = 'brand'


class BrandDetailAPIView(InventoryDetailAPIView):
    serializer_class = BrandSerializer
    queryset = Brand.objects.all()


class UnitOfMeasureListCreateAPIView(InventoryListCreateAPIView):
    serializer_class = UnitOfMeasureSerializer
    queryset = UnitOfMeasure.objects.all()
    response_key = 'unit'
    search_fields = ['name', 'code']


class UnitOfMeasureDetailAPIView(InventoryDetailAPIView):
    serializer_class = UnitOfMeasureSerializer
    queryset = UnitOfMeasure.objects.all()


class ProductTypeListCreateAPIView(InventoryListCreateAPIView):
    serializer_class = ProductTypeSerializer
    queryset = ProductType.objects.all()
    response_key = 'product_type'
    search_fields = ['name', 'code']


class ProductTypeDetailAPIView(InventoryDetailAPIView):
    serializer_class = ProductTypeSerializer
    queryset = ProductType.objects.all()
    protected_codes = ('goods',)


class StatusListCreateAPIView(InventoryListCreateAPIView):
    serializer_class = StatusSerializer
    queryset = Status.objects.all()
    response_key = 'status'
    search_fields = ['name', 'code']


class StatusDetailAPIView(InventoryDetailAPIView):
    serializer_class = StatusSerializer
    queryset = Status.objects.all()
    protected_codes = ('draft',)


class ProductListAPIView(InventoryListAPIView):
    serializer_class = ProductSerializer
    queryset = Product.objects.select_related('product_type', 'status', 'category', 'brand', 'unit', 'variant_of', 'currency')
    search_fields = ['name', 'sku', 'barcode', 'manufacturer', 'manufacturer_part_number']


class ProductCategoryListAPIView(InventoryListAPIView):
    serializer_class = ProductCategorySerializer
    queryset = ProductCategory.objects.all()
    search_fields = ['name', 'code']


class BrandListAPIView(InventoryListAPIView):
    serializer_class = BrandSerializer
    queryset = Brand.objects.all()


class UnitOfMeasureListAPIView(InventoryListAPIView):
    serializer_class = UnitOfMeasureSerializer
    queryset = UnitOfMeasure.objects.all()
    search_fields = ['name', 'code']


class ProductTypeListAPIView(InventoryListAPIView):
    serializer_class = ProductTypeSerializer
    queryset = ProductType.objects.all()
    search_fields = ['name', 'code']


class StatusListAPIView(InventoryListAPIView):
    serializer_class = StatusSerializer
    queryset = Status.objects.all()
    search_fields = ['name', 'code']


class CurrencyListCreateAPIView(InventoryListCreateAPIView):
    serializer_class = CurrencySerializer
    queryset = Currency.objects.all()
    response_key = 'currency'
    search_fields = ['name', 'code', 'symbol']


class CurrencyListAPIView(InventoryListAPIView):
    serializer_class = CurrencySerializer
    queryset = Currency.objects.all()
    search_fields = ['name', 'code', 'symbol']


class CurrencyDetailAPIView(InventoryDetailAPIView):
    serializer_class = CurrencySerializer
    queryset = Currency.objects.all()


class ProductUpdateAPIView(InventoryUpdateAPIView):
    serializer_class = ProductSerializer
    queryset = ProductDetailAPIView.queryset


class ProductCategoryUpdateAPIView(InventoryUpdateAPIView):
    serializer_class = ProductCategorySerializer
    queryset = ProductCategoryDetailAPIView.queryset


class BrandUpdateAPIView(InventoryUpdateAPIView):
    serializer_class = BrandSerializer
    queryset = BrandDetailAPIView.queryset


class UnitOfMeasureUpdateAPIView(InventoryUpdateAPIView):
    serializer_class = UnitOfMeasureSerializer
    queryset = UnitOfMeasureDetailAPIView.queryset


class ProductTypeUpdateAPIView(InventoryUpdateAPIView):
    serializer_class = ProductTypeSerializer
    queryset = ProductTypeDetailAPIView.queryset


class StatusUpdateAPIView(InventoryUpdateAPIView):
    serializer_class = StatusSerializer
    queryset = StatusDetailAPIView.queryset


class CurrencyUpdateAPIView(InventoryUpdateAPIView):
    serializer_class = CurrencySerializer
    queryset = CurrencyDetailAPIView.queryset
