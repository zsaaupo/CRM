from dataclasses import dataclass

from .models import Brand, Currency, Product, ProductCategory, ProductType, Status, UnitOfMeasure


@dataclass(frozen=True)
class CatalogResource:
    key: str
    path: str
    singular: str
    plural: str
    model: type
    columns: tuple

    def permission(self, action):
        return f'{self.model._meta.app_label}.{action}_{self.model._meta.model_name}'


# Insertion order is also the inventory sidebar order: products first.
RESOURCES = (
    CatalogResource('product', 'products', 'product', 'products', Product,
                    (('name', 'Product', 'identity'), ('sku', 'SKU', 'text'),
                     ('product_type', 'Type', 'text'), ('status', 'Status', 'status'),
                     ('sale_price', 'Sale price', 'price'))),
    CatalogResource('category', 'categories', 'category', 'categories', ProductCategory,
                    (('name', 'Name', 'text'), ('code', 'Code', 'text'),
                     ('description', 'Description', 'text'), ('is_active', 'Status', 'status'))),
    CatalogResource('brand', 'brands', 'brand', 'brands', Brand,
                    (('name', 'Name', 'text'), ('website', 'Website', 'text'), ('is_active', 'Status', 'status'))),
    CatalogResource('unit', 'units', 'unit', 'units', UnitOfMeasure,
                    (('name', 'Name', 'text'), ('code', 'Code', 'text'), ('is_active', 'Status', 'status'))),
    CatalogResource('product-type', 'product-types', 'product type', 'product types', ProductType,
                    (('name', 'Name', 'text'), ('code', 'Code', 'text'), ('is_active', 'Status', 'status'))),
    CatalogResource('status', 'statuses', 'status', 'statuses', Status,
                    (('name', 'Name', 'text'), ('code', 'Code', 'text'), ('is_active', 'Status', 'status'))),
    CatalogResource('currency', 'currencies', 'currency', 'currencies', Currency,
                    (('name', 'Name', 'text'), ('code', 'Code', 'text'), ('symbol', 'Symbol', 'text'),
                     ('exchange_rate', 'Exchange rate', 'text'), ('is_active', 'Status', 'status'))),
)
RESOURCE_MAP = {resource.key: resource for resource in RESOURCES}
