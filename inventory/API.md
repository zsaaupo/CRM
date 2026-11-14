# Inventory JSON APIs

The inventory app follows the account app's `serializers.py`, `api_views.py` and
`api_urls.py` structure. All URLs below begin with `/inventory/api/`.

Model views inherit `BaseListCreateAPIView`, `BaseListAPIView`,
`BaseDetailAPIView`, and `BaseUpdateAPIView` from `common/view.py`. Shared model
validation and audit field definitions live in `common/serializers.py`.

Products use `common.models.ProductStatus`. Purchases and sales use its fieldless
`TradeStatus` proxy subclass; both models share the same status records.
Existing `/inventory/api/statuses/` routes remain available, but their permissions
are `common.view_productstatus`, `common.add_productstatus`, `common.change_productstatus`, and
`common.delete_productstatus`. The rename migration preserves existing records and
permission grants. Referenced statuses and the default draft status cannot be
deleted. Status codes remain immutable through the APIs.

| Model | List / create | Detail / update / delete | Permission model name |
| --- | --- | --- | --- |
| Product | `products/` | `products/<id>/` | `product` |
| ProductCategory | `categories/` | `categories/<id>/` | `productcategory` |
| Brand | `brands/` | `brands/<id>/` | `brand` |
| UnitOfMeasure | `units/` | `units/<id>/` | `unitofmeasure` |
| ProductType | `product-types/` | `product-types/<id>/` | `producttype` |
| Status | `statuses/` | `statuses/<id>/` | `status` |
| Currency | `currencies/` | `currencies/<id>/` | `currency` |

- List: GET. Responses contain `count`, `next`, `previous`, `results` (20 per page).
- Create: POST. Returns 201 with `message` and the created resource object.
- Detail: GET. Returns the resource object.
- Update: PUT or PATCH. Returns the saved resource object.
- Delete: DELETE. Returns 204 with no response body.
- List search: `?q=term`, case-insensitive. Pagination: `?page=2`.

Create response keys are `product`, `category`, `brand`, `unit`, `product_type`
and `status`, respectively. Detail URLs use numeric primary keys.

## Dedicated update endpoints

These endpoints accept PUT, PATCH, and OPTIONS. They require an active session,
`inventory.change_<model>` (directly or through a group), and CSRF for writes.
They do not require separate view/add/delete grants. OPTIONS also requires the
change permission. GET, HEAD, POST, and DELETE return 405 for authenticated users.

| Model | Update URL |
| --- | --- |
| Product | `/inventory/api/products/<id>/update/` |
| ProductCategory | `/inventory/api/categories/<id>/update/` |
| Brand | `/inventory/api/brands/<id>/update/` |
| UnitOfMeasure | `/inventory/api/units/<id>/update/` |
| ProductType | `/inventory/api/product-types/<id>/update/` |
| Status | `/inventory/api/statuses/<id>/update/` |
| Currency | `/inventory/api/currencies/<id>/update/` |

Use PATCH for selected fields; PUT must include the serializer's required fields.
Both return 200 with the updated object, without a message/resource wrapper.
The server sets modified_by; id, public_id, and audit fields are not writable.
Product type, status, and currency codes remain immutable. Validation errors return
400 and missing records return 404. Product image uploads support multipart data;
PATCH `{"image": null}` as JSON clears the product image reference.

Example: `PATCH /inventory/api/brands/1/update/`

```json
{
  "name": "Updated brand",
  "is_active": true
}
```

The existing detail endpoints retain their GET/PUT/PATCH/DELETE behavior, so
existing clients and currency edit pages continue to work.

## Reference options and read-only lists

Product editing uses `GET /inventory/api/products/<id>/options/`. It requires
both `inventory.view_product` and `inventory.change_product`, without requiring
add permission. It returns the same minimal reference fields as creation options.
The current product is excluded from base-product choices; a product with
existing variants receives no base-product choices. Missing products return 404.

Product creation uses `GET /inventory/api/products/options/` for dropdown data.
It requires `inventory.add_product` (including group grants) and accepts only
GET/HEAD/OPTIONS. It returns `product_type`, `status`, `category`, `brand`, `unit`,
`currency`, and `variant_of` arrays. Each item has only `value` and `label`;
types/statuses/currencies use string codes, while other values are numeric IDs.
Base-product choices exclude variants. All choices retain the catalog's existing
behavior of including inactive records. Empty reference tables return empty
arrays. This permission does not authorize management of those models, and the
endpoint returns no prices, exchange rates, audit fields, or specifications.

These endpoints use ListAPIView and accept only GET, HEAD and OPTIONS:

| Model | Read-only list URL |
| --- | --- |
| Product | `/inventory/api/products/list/` |
| ProductCategory | `/inventory/api/categories/list/` |
| Brand | `/inventory/api/brands/list/` |
| UnitOfMeasure | `/inventory/api/units/list/` |
| ProductType | `/inventory/api/product-types/list/` |
| Status | `/inventory/api/statuses/list/` |
| Currency | `/inventory/api/currencies/list/` |

They require the corresponding `inventory.view_<model>` permission and an active
session, using the same permission classes as the existing endpoints. Search
(`?q=term`), pagination (`?page=2`, 20 per page), fields and ordering are shared
with the existing list/create views. Authenticated writes return 405 without
modifying data. Existing list/create and detail URLs continue working.

## Authentication and permissions

All endpoints use the same classes as AddUserAPIView:
`SessionAuthentication`, `IsAuthenticated`, and `HasRequiredPermissions`.
Their required permissions use each inventory model, not `auth.add_user`:

| Method | Required permission |
| --- | --- |
| GET / HEAD / OPTIONS | `inventory.view_<model>` |
| POST | `inventory.add_<model>` |
| PUT / PATCH | `inventory.change_<model>` |
| DELETE | `inventory.delete_<model>` |

For example, creating a category requires `inventory.add_productcategory` and
listing products requires `inventory.view_product`. Permissions may be inherited
from a group or granted directly. Active superusers retain full access; staff
status alone grants none. Missing/inactive sessions or permissions return 403.

Use the existing [account login flow](../AUTH_API.md) and retain cookies.
Writes require `X-CSRFToken` with the current token returned after login.
JSON requests use Content-Type application/json. Responses are not cached.

## Create examples

`POST /inventory/api/units/`

```json
{"name": "Pieces", "code": "PCS", "is_active": true}
```

`POST /inventory/api/categories/`

```json
{"name": "Electronics", "code": "ELEC", "description": "Electronic products"}
```

`POST /inventory/api/brands/`

```json
{"name": "Example Brand", "website": "https://example.com"}
```

`POST /inventory/api/product-types/`

```json
{"name": "Rental", "code": "rental", "description": "Rental items"}
```

`POST /inventory/api/statuses/`

```json
{"name": "Awaiting review", "code": "awaiting-review"}
```

`POST /inventory/api/products/`

```json
{
  "sku": "ITEM-001",
  "name": "Example product",
  "currency": "BDT",
  "unit": 1,
  "product_type": "goods",
  "status": "draft",
  "sale_price": "1500.0000",
  "cost_price": "1000.0000",
  "Whole_sale_price": "1200.0000",
  "product_tax": "15.0000",
  "attributes": {"color": "blue"}
}
```

Replace `unit` with an existing unit's numeric ID. Optional `brand`, `category`
and `variant_of` also use numeric IDs. Product type and status use lookup **code
strings**, defaulting to goods and draft when omitted. The existing
`Whole_sale_price` field spelling is preserved. Money/percentage responses use
decimal strings. Minimum required product inputs are SKU, name, currency and unit.

For product images, send multipart/form-data with `image` as File and the other
fields as Text. Let Postman set Content-Type/boundary automatically. For multipart
attributes, send a JSON string such as `{"color":"blue"}`. Images use the existing
media settings. An omitted image is optional; `image: null` in JSON clears it.

## Validation and protected data

Model business rules run for creates and updates, including PATCH merged with
the existing record. Invalid fields, duplicate identifiers, price/tax errors,
variant errors and database integrity conflicts return 400 without partial
database changes. Missing records return 404.

`id`, `public_id`, timestamps, `create_by` and `modified_by` cannot be assigned
by clients. Create sets both actor fields from the logged-in username; updates
preserve the creator and set the modifying actor. Responses include audit fields.

ProductType and Status codes cannot be changed after creation, matching the admin
rules. The default goods/draft records cannot be deleted. Records referenced by
products or variants cannot be deleted either: these conflicts return 409.
Names, descriptions and active flags remain editable with change permission.

## Currencies

POST `/inventory/api/currencies/` creates a currency:

```json
{
  "code": "USD",
  "name": "US dollar",
  "symbol": "$",
  "exchange_rate": "120.00000000",
  "is_active": true
}
```

This rate is an illustrative value entered by the operator, not a live market
quote. One currency unit equals `exchange_rate` base-currency units. The base
currency comes from `INVENTORY_BASE_CURRENCY` (default BDT) and is returned in
`base_currency`. Its own rate must be 1. Rates must be positive, with at most
eight decimal places; new currencies require a rate. Unknown migrated rates
remain null until updated.

Create responses use `{"message": "...", "currency": {...}}`. GET, PUT, PATCH,
and DELETE use `/inventory/api/currencies/<id>/`. PATCH may change the name,
symbol, exchange_rate, or is_active; code is immutable. GET lists require
`inventory.view_currency`, POST requires `inventory.add_currency`, PUT/PATCH
require `inventory.change_currency`, and DELETE requires `inventory.delete_currency`.
All writes require session authentication and CSRF. Deleting a referenced
currency returns 409.

Product payloads still use `"currency": "USD"`, but the code must now exist in
Currency. Currency detail URLs use numeric IDs. Product prices stay in their
selected currency; updating a rate does not automatically convert prices.

## Product detail page

Each product list row has an eye icon linking to `/inventory/products/<id>/`.
The page loads `/inventory/api/products/<id>/` and requires
`inventory.view_product`. It shows identification, classification, image,
manufacturing details, specifications, prices, tax and record history. The edit
link additionally requires `inventory.change_product`.

The product detail API includes read-only `product_type_name`, `status_name`,
`category_name`, `brand_name`, `unit_name`, `unit_code`, `currency_name`,
`variant_of_name` and `variant_of_sku` alongside the existing relation codes/IDs.
Optional relation labels are null when not set. These labels do not require
catalog-management permissions and cannot be changed through product writes.
