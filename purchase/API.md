# Purchase APIs

All routes start with `/purchase/api/`. Session authentication is required;
send the logged-in session cookie and a valid `X-CSRFToken` on writes.
JSON, form data and multipart requests are supported.

| Resource | List / create | Retrieve / update / delete |
| --- | --- | --- |
| Purchase orders | `GET /purchase-orders/`, `POST /purchase-orders/` | `GET`, `PUT`, `PATCH`, `DELETE /purchase-orders/<id>/` |
| Purchase order items | `GET /purchase-order-items/`, `POST /purchase-order-items/` | `GET`, `PUT`, `PATCH`, `DELETE /purchase-order-items/<id>/` |

Each resource also provides a read-only `GET <resource>/list/` endpoint and a
dedicated `PUT` / `PATCH <resource>/<id>/update/` endpoint, matching inventory.
The URL namespace is `purchase`.

## Permissions

Authenticated users need the matching Django permission, granted directly or
through their group. Superusers retain Django's normal permission behavior.

| Method | Purchase order | Purchase order item |
| --- | --- | --- |
| GET / HEAD | `purchase.view_purchaseorder` | `purchase.view_purchaseorderitem` |
| POST | `purchase.add_purchaseorder` | `purchase.add_purchaseorderitem` |
| PUT / PATCH | `purchase.change_purchaseorder` | `purchase.change_purchaseorderitem` |
| DELETE | `purchase.delete_purchaseorder` | `purchase.delete_purchaseorderitem` |

OPTIONS uses view permission except on the dedicated update endpoints, which use
change permission. Permission failures return 403.
Creating an order with nested `items` also requires
`purchase.add_purchaseorderitem`.

## Create an order

`POST /purchase/api/purchase-orders/`

```json
{
  "supplier": 1,
  "order_date": "2026-11-14",
  "expected_date": "2026-11-20",
  "currency": "BDT",
  "exchange_rate": "1.00000000",
  "status": "draft",
  "supplier_reference": "SUP-1001",
  "shipping_cost": "50.0000",
  "notes": "Deliver during office hours."
}
```

Currency and status use existing record **codes**. Supplier is an optional
Party **ID** (replace `1` with a valid ID), or null. The selected party must have
`is_supplier=true` and must not be soft-deleted. A customer-only party is rejected
with 400, including on PUT/PATCH. Parties with both roles are eligible. The
`is_active` flag does not change supplier eligibility. Responses include
read-only `supplier_name` and `supplier_code`; lists and details display those
labels. Status defaults to `draft`; the order date
defaults to today. Exchange rate is required and must be positive; base-currency
orders require rate 1. Expected date cannot precede order date.

A successful creation returns 201 with `message` and `purchase_order` keys.
`order_number`, IDs, audit fields, `subtotal`, `tax_total` and `grand_total` are
read-only. The order number is generated automatically.

To create the order and its items in one transaction, include an `items` array
in this POST body. Each item uses the fields in the item example below, except
`purchase_order`, which the server supplies. The array must contain at least one
item and line numbers must be unique. If any header or item is invalid, nothing
is saved. Omitting `items` still permits header-only API creation.

## Create an item

`POST /purchase/api/purchase-order-items/`

```json
{
  "purchase_order": 1,
  "line_number": 1,
  "product": 7,
  "description": "Keyboard",
  "unit": 1,
  "quantity": "3.000000",
  "unit_price": "100.0000",
  "discount_amount": "20.0000",
  "tax_rate": "15.0000"
}
```

Replace IDs with existing records. Unit must match the product's unit. Quantity
and line number must be positive, and line numbers must be unique within an
order. Prices and discounts cannot be negative; discount is an amount for the
whole line and cannot exceed quantity multiplied by unit price. Tax rate must
be between 0 and 100.

A successful creation returns 201 with `message` and `purchase_order_item` keys.
The response includes read-only `net_amount`, `tax_amount` and `line_total`.
For this example they are `280.0000`, `42.0000` and `322.0000`.
IDs and audit fields are also read-only; the server records the current user.

## Lists and updates

Lists return `count`, `next`, `previous` and `results`, with 20 records per page.
Use `?page=2` for another page and `?q=keyboard` to search.
Orders search order number, supplier name/code, supplier reference and notes; they sort by
order date descending, then ID descending. Items search order number, product
name/SKU and description; they sort by order ID, line number, then ID.

`GET /purchase/api/purchase-order-items/?purchase_order=1` filters the items of an
order. Combine it with `q` or `page`. Invalid order filter values return 400.

PATCH accepts only changed fields; PUT accepts the required writable fields.
Responses contain the updated record directly. Validation returns 400 with
field errors; missing records return 404. Existing items are updated through
their own endpoints; nested writes are supported only when creating an order.

## Web pages and reference data

The sidebar's Purchase section links to `/purchase/purchase-orders/` and
`/purchase/purchase-orders/add/`. The list uses the paginated read-only API;
the create page loads choices from `GET /purchase/api/purchase-orders/options/`
and submits a JSON order with nested items to the order creation API. Successful
creation redirects to the purchase list. Both pages require order view
permission; creation additionally requires order and item add permissions.

The options API requires both add permissions and returns `supplier`, `currency`,
`status`, `product` and `base_currency`. Supplier choices include only non-deleted
parties with `is_supplier=true`, as `{"value": 1, "label": "Name (CODE)"}`.
The create page uses these choices in an optional dropdown. Party view permission
is not required just to choose a supplier for a purchase. Products include their unit ID and label;
currencies include their current exchange rate as a suggested value. An unknown
rate must be entered manually. Catalog view permissions are not required just
to populate this form. Choices are loaded when the page opens, without caching.

Each purchase list row has an eye icon linking to
`/purchase/purchase-orders/<id>/`. The detail page loads the order detail API,
including its read-only `currency_name` and `status_name`, and shows dates,
supplier information, notes, exchange rate, totals and record history.

Purchase view permission allows the summary. The product lines additionally
require `purchase.view_purchaseorderitem`; they use the existing item list API
filtered by `purchase_order`, with pagination so orders with more than 20 lines
remain fully accessible. Item responses include read-only `product_name`,
`product_sku`, `unit_name` and `unit_code`. Product links within these lines are
shown only to users with `inventory.view_product`.

## Deletion and totals

DELETE performs hard deletion and returns 204. An order referenced by items
returns 409; delete those items first. Related products, units, currencies and
statuses remain protected by their model relationships.

Order totals reflect current items and shipping cost. Creating, updating or
deleting purchase records does not change warehouse stock. Status is a shared
lookup value; this API does not implement confirmation rules or lock records
based on status.

Suppliers are protected references. Party deletion still soft-deletes only the
party; existing purchases retain the supplier ID/name/code, including in the
purchase detail API. Unrelated PATCH requests continue to work for these orders.
Selecting a deleted party or one whose supplier role has been removed is rejected
on new assignments, even if its ID is supplied directly. Omitting the supplier
field preserves the historical reference; supplying null clears it.
