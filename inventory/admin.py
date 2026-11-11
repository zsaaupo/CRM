from django.contrib import admin
from django.core.exceptions import PermissionDenied

from .models import Brand, Product, ProductCategory, ProductType, Status, UnitOfMeasure


class CatalogAdmin(admin.ModelAdmin):
    readonly_fields = ['created_at', 'updated_at', 'create_by', 'modified_by']

    def save_model(self, request, obj, form, change):
        if not change:
            obj.create_by = request.user.get_username()
        obj.modified_by = request.user.get_username()
        super().save_model(request, obj, form, change)


class ProductLookupAdmin(CatalogAdmin):
    list_display = ['name', 'code', 'is_active']
    search_fields = ['name', 'code']
    list_filter = ['is_active']
    default_codes = ()

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        return fields + ['code'] if obj else fields

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and not (obj and obj.code in self.default_codes)

    def delete_queryset(self, request, queryset):
        if queryset.filter(code__in=self.default_codes).exists():
            raise PermissionDenied('Default product lookup records cannot be deleted.')
        super().delete_queryset(request, queryset)


@admin.register(ProductType)
class ProductTypeAdmin(ProductLookupAdmin):
    default_codes = ('goods',)


@admin.register(Status)
class StatusAdmin(ProductLookupAdmin):
    default_codes = ('draft',)


@admin.register(ProductCategory)
class ProductCategoryAdmin(CatalogAdmin):
    list_display = ['name', 'code', 'is_active']
    search_fields = ['name', 'code']
    list_filter = ['is_active']


@admin.register(Brand)
class BrandAdmin(CatalogAdmin):
    list_display = ['name', 'website', 'is_active']
    search_fields = ['name']
    list_filter = ['is_active']


@admin.register(UnitOfMeasure)
class UnitOfMeasureAdmin(CatalogAdmin):
    list_display = ['name', 'code', 'is_active']
    search_fields = ['name', 'code']
    list_filter = ['is_active']


@admin.register(Product)
class ProductAdmin(CatalogAdmin):
    list_display = ['sku', 'name', 'product_type', 'status', 'category', 'sale_price', 'currency', 'unit']
    list_filter = ['status', 'product_type', 'category', 'brand']
    search_fields = ['sku', 'name', 'barcode', 'manufacturer_part_number']
    autocomplete_fields = ['product_type', 'status', 'category', 'brand', 'unit', 'variant_of']
    list_select_related = ['product_type', 'status', 'category', 'brand', 'unit']
    readonly_fields = ['public_id', *CatalogAdmin.readonly_fields]
    fieldsets = [
        ('Identification', {'fields': ('sku', 'name', 'barcode', 'public_id', 'product_type', 'status')}),
        ('Classification and content', {'fields': ('category', 'brand', 'unit', 'variant_of', 'description', 'image', 'manufacturer', 'manufacturer_part_number', 'attributes')}),
        ('Pricing and tax', {'fields': ('currency', 'cost_price', 'sale_price', 'minimum_sale_price', 'Whole_sale_price', 'product_tax', 'is_returnable')}),
        ('Audit', {'fields': ('create_by', 'modified_by', 'created_at', 'updated_at'), 'classes': ('collapse',)}),
    ]

