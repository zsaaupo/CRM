# Product catalog

Products use separate ProductType and Status tables rather than TextChoices.
Both have a unique stable code, display name, description, active flag and the
shared common TimeStampedModel audit fields. Manage them through Django admin.

The migration seeds these records:

- ProductType: goods, service, digital.
- Status: draft, active, discontinued, archived.

New products still default to goods and draft. Product foreign keys reference
lookup codes, so existing text values retain their meaning. The public Python
attributes product_type and status now return related model objects; their
product_type_id and status_id values are the string codes, not numeric row IDs.
Referenced lookup rows are protected from deletion. Codes are read-only after
creation in admin; display names and descriptions remain editable. Admin also
protects the default goods and draft records against deletion. New type/status
records do not require another model migration.

## Current product fields

- Identity: public UUID, SKU, name, barcode, product type and status.
- Classification: category, brand, unit and optional base product for variants.
- Content: description, image, manufacturer, part number and JSON attributes.
- Prices: currency, cost_price, sale_price, minimum_sale_price, Whole_sale_price.
- Tax and returns: product_tax percentage, is_returnable.
- Audit: created_at, updated_at, create_by, modified_by from common.models.

The existing Whole_sale_price spelling is preserved. Images upload to
products_image/. SKU matching ignores case/surrounding whitespace; nonblank
barcodes must be unique. Prices cannot be negative; sale price respects the
optional minimum; product_tax must be between 0 and 100. Variants must have the
same product type as their base product and cannot be nested.

The stock, unit precision, dimensions and other fields removed from the product
source are not restored. Migration 0003 reconciles their obsolete constraints,
admin references and database columns, while introducing the lookup tables.
Before those columns are removed, existing sale_tax_rate values are carried into
the replacement product_tax field. On databases with legacy inventory data,
review/back up any removed fields before applying that schema cleanup.

## Usage

Django save()/objects.create() do not call full_clean() automatically. Admin
forms perform model validation. Programmatic callers should validate explicitly:

```python
from decimal import Decimal
from inventory.models import Product, ProductType, Status, UnitOfMeasure

unit = UnitOfMeasure.objects.create(name='Pieces', code='PCS')
product = Product(
    sku='ITEM-001', name='Example product', currency='BDT', unit=unit,
    product_type=ProductType.objects.get(code='goods'),
    status=Status.objects.get(code='active'), sale_price=Decimal('1500.00'),
    create_by='operator@example.com', modified_by='operator@example.com',
)
product.full_clean()
product.save()
```

The shared actor fields currently require values during full_clean() because
common.models defines them without blank=True. Inventory admin fills them from
the signed-in username and excludes them from editable forms.

Lookup names and active flags are catalog metadata, not a workflow engine.
A status named archived does not automatically hide or archive a product.
Warehouse balances, stock movements, suppliers, price lists, and accounting
transactions remain future related models. All six catalog models have JSON CRUD
APIs; see [API.md](API.md) for routes, permissions and Postman examples.

## Inventory pages

The dashboard sidebar includes Inventory for users with catalog view permissions.
Products appear first, with a separate Add product shortcut. Each model list also
has an Add button when the user has its add permission. Pages reuse the dashboard
styles and submit to the existing session-authenticated, CSRF-protected APIs.
Successful creation returns to the corresponding list.

| Model | List | Add |
| --- | --- | --- |
| Product | `/inventory/products/` | `/inventory/products/add/` |
| ProductCategory | `/inventory/categories/` | `/inventory/categories/add/` |
| Brand | `/inventory/brands/` | `/inventory/brands/add/` |
| UnitOfMeasure | `/inventory/units/` | `/inventory/units/add/` |
| ProductType | `/inventory/product-types/` | `/inventory/product-types/add/` |
| Status | `/inventory/statuses/` | `/inventory/statuses/add/` |

Lists require the model's `view` permission. Add pages require both `view` and
`add`, ensuring the user can open the destination after saving. Group grants and
superuser access work through Django's standard permissions. Lists support search
and pagination. Product creation includes an optional image and catalog lookups;
add at least one unit before creating products.
