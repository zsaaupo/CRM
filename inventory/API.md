# Inventory JSON APIs

The inventory app follows the account app's `serializers.py`, `api_views.py` and
`api_urls.py` structure. All URLs below begin with `/inventory/api/`.

| Model | List / create | Detail / update / delete | Permission model name |
| --- | --- | --- | --- |
| Product | `products/` | `products/<id>/` | `product` |
| ProductCategory | `categories/` | `categories/<id>/` | `productcategory` |
| Brand | `brands/` | `brands/<id>/` | `brand` |
| UnitOfMeasure | `units/` | `units/<id>/` | `unitofmeasure` |
| ProductType | `product-types/` | `product-types/<id>/` | `producttype` |
| Status | `statuses/` | `statuses/<id>/` | `status` |

- List: GET. Responses contain `count`, `next`, `previous`, `results` (20 per page).
- Create: POST. Returns 201 with `message` and the created resource object.
- Detail: GET. Returns the resource object.
- Update: PUT or PATCH. Returns the saved resource object.
- Delete: DELETE. Returns 204 with no response body.
- List search: `?q=term`, case-insensitive. Pagination: `?page=2`.

Create response keys are `product`, `category`, `brand`, `unit`, `product_type`
and `status`, respectively. Detail URLs use numeric primary keys.

## Dedicated read-only list views

These endpoints use ListAPIView and accept only GET, HEAD and OPTIONS:

| Model | Read-only list URL |
| --- | --- |
| Product | `/inventory/api/products/list/` |
| ProductCategory | `/inventory/api/categories/list/` |
| Brand | `/inventory/api/brands/list/` |
| UnitOfMeasure | `/inventory/api/units/list/` |
| ProductType | `/inventory/api/product-types/list/` |
| Status | `/inventory/api/statuses/list/` |

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
