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
  "payment_account": 2,
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

`payment_account` is required. Use an active base-currency account assigned to
the receiving warehouse or a shared account within the user's scope.

A successful creation returns 201 with `message` and `purchase_order` keys.
`order_number`, IDs, audit fields, `subtotal`, `tax_total` and `grand_total` are
read-only. The order number is generated automatically.

To create the order and its items in one transaction, include an `items` array
in this POST body. Each item uses the fields in the item example below, except
`purchase_order`, which the server supplies. The array must contain at least one
item and line numbers must be unique. If any header or item is invalid, nothing
is saved. Omitting `items` still permits header-only API creation.

## Import a purchase from CSV

Open **Purchase list → Import CSV** to download a template and upload product
lines for one order. `GET /purchase/api/purchase-orders/import/` downloads the
template. `POST /purchase/api/purchase-orders/import/` accepts
`multipart/form-data` with `file`, `currency`, `exchange_rate`, `payment_account`,
and optional order header fields such as `supplier` and `warehouse`.
`GET /purchase/api/purchase-orders/import/options/` loads the header choices.
These routes require both order and item add permissions and use the normal
warehouse scope.

The UTF-8 CSV requires `sku` and `quantity`. Optional columns are `unit_price`,
`discount_amount`, `tax_rate`, and `description`. The SKU identifies an existing
product and its unit. A blank price uses the catalog cost when the currencies
match. Supply a price in the purchase currency when they differ. Blank discounts
and tax rates use zero. The order preserves file order. The file limit is 1,000
product rows and 5 MB.

Invalid lines return 400 with CSV row numbers under `file`. A successful import
returns 201 with `purchase_order`. The imported order starts unreceived, and the
normal Receive action adds stock and posts payment.

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
`status`, `product`, `base_currency` and `payment_account`. Payment-account
choices include their warehouse IDs. Supplier choices include only non-deleted
parties with `is_supplier=true`, as `{"value": 1, "label": "Name (CODE)"}`.
The create page uses these choices in an optional dropdown. Party view permission
is not required just to choose a supplier for a purchase. Products include their unit ID, label,
and `cost_price` as a decimal string. Selecting a product fills the line's unit price
from that cost price; the price remains editable. Opening an existing purchase
preserves its saved line prices until another product is selected.
Currencies include their current exchange rate as a suggested value. An unknown
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

Warehouse scope and CRUD permissions are inherited from the user's single role. Specific-warehouse
users can only list/read/create/change/delete their own warehouse's orders/items
and receive those purchases. Missing warehouse on creation defaults to their
assignment; explicit null or another warehouse is rejected. Options show only
their active assigned warehouse and the form locks its dropdown. Unassigned
users are denied; all-warehouse users/superusers retain broader scope. See
[warehouse scope rules](../warehouse/API.md#warehouse-scope-enforcement).

DELETE performs hard deletion of unreceived records and returns 204. Deleting
an order with items requires both order and item delete permissions; its items
and header are removed atomically. Received records reject deletion with 409.
Other protected references still return 409 and roll back the deletion.
Related products, units, currencies and statuses remain protected.

The purchase list shows a red delete icon with both delete permissions. Edit and
delete actions remain visible but disabled once is_received is true, including
immediately after a successful Receive action.

Order totals reflect current items and shipping cost. Creating, updating or
deleting unreceived purchase records does not change warehouse stock. Status is
a shared lookup value; receipt is controlled separately by is_received.

Suppliers are protected references. Party deletion still soft-deletes only the
party; existing purchases retain the supplier ID/name/code, including in the
purchase detail API. Unrelated PATCH requests continue to work for these orders.
Selecting a deleted party or one whose supplier role has been removed is rejected
on new assignments, even if its ID is supplied directly. Omitting the supplier
field preserves the historical reference; supplying null clears it.

## Edit purchases

Purchase list rows show a pencil edit action for users with order view/change
and item view/add/change/delete permissions. The action is hidden when that
row's is_received flag is true. It opens `/purchase/purchase-orders/<id>/edit/`,
which reuses the creation template, styles and API-driven form controller.
Successful saves redirect to the purchase list.

The edit form loads the order detail and every page of its item list before
enabling saving. Reference choices come from GET
`/purchase/api/purchase-orders/edit-options/`, requiring order view/change and
item view permissions; creating new orders is not required. Existing supplier
links, prices, exchange-rate snapshots, item IDs and creation audits are retained.

PUT/PATCH on the existing order detail/update APIs now accept an optional nested
`items` list. Omit items for a header-only update. When supplied, it represents
the complete desired item list and must contain at least one line:

```json
{
  "notes": "Updated before receipt",
  "items": [
    {
      "id": 45,
      "line_number": 1,
      "product": 3,
      "unit": 1,
      "quantity": "5.000000",
      "unit_price": "10.0000",
      "discount_amount": "0.0000",
      "tax_rate": "0.0000",
      "description": "Agreed product"
    }
  ]
}
```

An existing item ID updates that line, a line without an ID creates a new item,
and omitted existing IDs delete those lines. IDs must be unique and belong to
this purchase; new purchases cannot submit existing IDs. Add/change/delete item
permissions are checked for the actions actually performed. Line-number swaps
retain IDs. Header and line changes commit together; any failure rolls back all
changes. Editing does not increase stock.

Received orders are blocked by the API even if an edit URL is entered directly
or receipt occurs while the editor is open. The form then remains disabled with
a read-only message. JavaScript interaction tests are available with
`node purchase/tests/purchase_edit_ui.mjs`.

## Receive a purchase and post payment

Purchase responses expose `is_received` (read-only, default false), `warehouse`
(optional warehouse ID before receipt), and `warehouse_name` (read-only). Ordinary
create/update endpoints cannot set or reset is_received. Set a receiving warehouse
when creating the order or PATCH it onto an existing unreceived purchase:

```http
PATCH /purchase/api/purchase-orders/12/update/
Content-Type: application/json

{"warehouse": 3, "payment_account": 2}
```

The purchase creation page also loads active warehouses through the existing
purchase options API. A draft can omit a warehouse, but receipt requires one.
Assign a payment account to any older draft before receiving it.

```http
POST /purchase/api/purchase-orders/receive/
Content-Type: application/json

{
  "purchase_id": 12,
  "is_received": true
}
```

Requires SessionAuthentication, IsAuthenticated, the existing
`purchase.change_purchaseorder` permission (direct or group), and X-CSRFToken.
This endpoint accepts POST/OPTIONS. Responses are marked no-store.

Purchase list rows include a receive icon action for users with purchase change
permission. It is enabled only when is_received=false. Clicking sends the request
above with the current CSRF token and disables the button while it is pending.
On success the row is updated immediately: the receive action stays disabled and
its edit link disappears. Already received rows also show a disabled receive
action. Errors are shown above the table and allow a safe retry; search and
pagination retain the correct button state if refreshed during a request.
JavaScript coverage is available with `node purchase/tests/purchase_receive_ui.mjs`.

Receiving performs one atomic operation:

1. Claim the unreceived purchase. Reject a missing/inactive receiving warehouse,
   empty order or a line whose unit no longer matches its product.
2. Sum quantities by product ID across all purchase lines.
3. Create the warehouse/product Stock balance if absent, otherwise increment its
   quantity. Different purchases/suppliers share that same balance; different
   warehouses have separate balances.
4. Post the purchase total as an outflow on the selected account's statement.
   For foreign-currency orders, the API uses the saved exchange rate to convert
   the total into the base currency. A zero total produces no ledger entry.
5. Set is_received=true and server audit fields. The header and all its items
   become read-only through the APIs. PUT/PATCH/DELETE, adding new items and moving
   items into/out of the received order are blocked. GET/list access is unchanged.

The API returns 200 with `message`, `received_now` and `purchase_order`. First
success returns received_now=true. Repeating the request returns false and does
not add stock again, post another payment or change the receipt audit timestamp.
This also prevents duplicates from concurrent receipt requests. A concurrent item edit either
finishes before receipt or is blocked; it cannot rewrite received quantities.

False/missing is_received, invalid IDs, missing or ineligible payment accounts,
insufficient funds, invalid quantities or unsupported stock precision return 400. A
missing receiving warehouse also returns 400. Missing purchases return
404; permission/CSRF failures return 403. Locked writes and retryable database
contention return 409. A failure rolls back the receipt, stock, and payment
writes. Retry a busy request with the
same purchase ID and true flag.

Receipt takes all items at their stored quantities, independently of trade status.
It does not automatically confirm/close the status, create stock movements or
allow partial receipts/unreceiving. Product totals across branches can be computed
by summing their Stock quantities; this endpoint updates the selected warehouse.

## Return a received purchase

Open **Purchase → Purchase returns** or select **Create return** on a received
purchase. A draft can return all or part of one or more lines from the same
purchase. Completed returns count against the received quantity for each line.

| Method | Path under `/purchase/api/purchase-returns/` | Result |
| --- | --- | --- |
| GET | `/` or `list/` | List and search returns in the user's warehouse scope |
| GET | `options/` | List received purchases in active warehouses |
| GET | `options/?purchase_order=42` | Get original lines and received, returned, and remaining quantities |
| POST | `/` | Create a draft |
| GET | `<id>/` or `<id>/options/` | Get a return or the options for editing its draft |
| PATCH, PUT | `<id>/` or `<id>/update/` | Update a draft |
| DELETE | `<id>/` | Delete a draft |
| POST | `complete/` | Deduct stock, record the supplier refund, and complete the return |

Draft creation accepts the purchase ID, original line IDs, and decimal quantities.

```json
{
  "purchase_order": 42,
  "items": [{"purchase_order_item": 81, "quantity": "2.500000"}]
}
```

Send `{"return_id": 7, "is_completed": true}` to complete a draft. Completion
deducts stock from the original receiving warehouse and posts the supplier
refund to the account that paid for the purchase. The refund covers returned
merchandise at its original discounted and taxed value using the exchange rate
saved on the purchase. Partial return values are rounded cumulatively to four
decimals. Earlier refunds reduce the amount available from the original payment.

The account statement shows the refund as an inflow with the return number as
its reference. The return detail includes the posted `refund_amount` and
`currency`. Older purchases with no recorded payment complete with a zero
refund. Stock deduction, refund posting, and completion commit together. If a
step fails, the draft remains editable. A repeated request returns
`completed_now: false` and reuses the original result.

The endpoints use role permissions and warehouse scope. Draft creation requires
view permissions for purchases and purchase items plus add permissions for
purchase returns and return items. Editing or deleting lines requires the
corresponding line permissions. Completion requires `purchase.change_purchasereturn`.
Session writes require CSRF.
