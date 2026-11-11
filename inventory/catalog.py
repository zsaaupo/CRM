from dataclasses import dataclass

from .forms import BrandForm, ProductCategoryForm, ProductForm, ProductTypeForm, StatusForm, UnitOfMeasureForm
from .models import Brand, Product, ProductCategory, ProductType, Status, UnitOfMeasure


@dataclass(frozen=True)
class CatalogResource:
    key: str
    path: str
    singular: str
    plural: str
    model: type
    form: type
    columns: tuple

    def permission(self, action):
        return f'{self.model._meta.app_label}.{action}_{self.model._meta.model_name}'


# Insertion order is also the inventory sidebar order: products first.
RESOURCES = (
    CatalogResource('product', 'products', 'product', 'products', Product, ProductForm,
                    (('name', 'Product', 'identity'), ('sku', 'SKU', 'text'),
                     ('product_type', 'Type', 'text'), ('status', 'Status', 'status'),
                     ('sale_price', 'Sale price', 'price'))),
    CatalogResource('category', 'categories', 'category', 'categories', ProductCategory, ProductCategoryForm,
                    (('name', 'Name', 'text'), ('code', 'Code', 'text'),
                     ('description', 'Description', 'text'), ('is_active', 'Status', 'status'))),
    CatalogResource('brand', 'brands', 'brand', 'brands', Brand, BrandForm,
                    (('name', 'Name', 'text'), ('website', 'Website', 'text'), ('is_active', 'Status', 'status'))),
    CatalogResource('unit', 'units', 'unit', 'units', UnitOfMeasure, UnitOfMeasureForm,
                    (('name', 'Name', 'text'), ('code', 'Code', 'text'), ('is_active', 'Status', 'status'))),
    CatalogResource('product-type', 'product-types', 'product type', 'product types', ProductType, ProductTypeForm,
                    (('name', 'Name', 'text'), ('code', 'Code', 'text'), ('is_active', 'Status', 'status'))),
    CatalogResource('status', 'statuses', 'status', 'statuses', Status, StatusForm,
                    (('name', 'Name', 'text'), ('code', 'Code', 'text'), ('is_active', 'Status', 'status'))),
)
RESOURCE_MAP = {resource.key: resource for resource in RESOURCES}
