# Sales APIs and pages

## Chalans

Open **Sales → Chalan** at `/sale/chalans/` to create, view, or edit a standalone
delivery record. Creating or editing a Chalan saves its details and product quantities.

| Route under `/sale/api/chalans/` | Required role permissions |
| --- | --- |
| `GET /`, `GET list/`, `GET <id>/` | `sale.view_chalan`. Detail includes `items` with `sale.view_chalanitem`. |
| `POST /` | `sale.add_chalan`, `sale.add_chalanitem` |
| `PUT` or `PATCH` on `<id>/` or `<id>/update/` | `sale.change_chalan`, plus the item add, change, or delete permission for each action applied to the lines. |
| `DELETE <id>/` | `sale.delete_chalan`, plus `sale.delete_chalanitem` if the Chalan has items. |
| `GET options/` | `sale.add_chalan`, `sale.add_chalanitem` |
| `GET edit-options/` | Chalan view and change, and item view, add, change, and delete permissions. |

Create with a `customer`, `shipping_address`, `warehouse`, and at least one `items` entry.
A user assigned to one warehouse can omit `warehouse` on create. Each line contains
`line_number`, `product`, `unit`, `quantity`, and an optional `description`. On edit,
omit `items` to keep existing lines, or send the full replacement list with `id` for
each existing line to retain. The list supports `q` and `page`. All endpoints enforce
the user's warehouse access.

Once the linked delivery reaches `delivered`, the Chalan is read-only. The API
returns `is_delivered` and rejects edits and deletions with HTTP 409. Deleting an
eligible Chalan removes its items, packing slip, and delivery history in one transaction.

Chalans use the regular Sales calculation. Send `currency`, `exchange_rate` and
`shipping_cost` on the header, and `unit_price`, `discount_amount`, and `tax_rate`
on each priced item. A foreign currency requires a positive rate. Amounts are in
the Chalan currency. The API calculates `subtotal`, `tax_total`, and `grand_total`
and returns `net_amount`, `tax_amount`, and `line_total` on item detail. Existing
quantity-only records start with zero prices, discounts, tax rates and shipping.

## Packing slips

Use **Generate packing slip** on a Chalan with items and open **Sales → Packing slip list**
at `/sale/packing-slips/`. Reprints use the Chalan's current items, prices, totals,
and shipping details.

| Route | Required role permissions |
| --- | --- |
| `GET /sale/api/packing-slips/` | `sale.view_chalan`, `sale.view_packingslip` |
| `POST /sale/api/chalans/<chalan_id>/packing-slip/` | the PDF permissions below, plus `sale.add_packingslip` |
| `GET /report/api/packing-slips/<slip_id>/pdf/` | `sale.view_chalan`, `sale.view_chalanitem`, `sale.view_packingslip` |

Generation returns HTTP 201 for a new slip and 200 with the existing slip on retry.
The list supports `q` and `page`. Each endpoint applies the Chalan warehouse scope.

## Couriers and deliveries

Create a courier at **Sales → Couriers → Create courier**. From a Chalan, choose
**Assign courier** and enter the tracking number when available. Open **Track delivery**
on the Chalan or select the record under **Sales → Deliveries** to record status updates.
The delivery links to the Chalan's packing slip. Generate the slip before recording handover.
The status history records the time, user, and optional note for each update.

| Route | Required role permissions |
| --- | --- |
| `GET /sale/api/couriers/` or `/sale/api/couriers/<id>/` | `sale.view_courier` |
| `POST /sale/api/couriers/` | `sale.add_courier` |
| `PATCH /sale/api/couriers/<id>/` | `sale.change_courier` |
| `GET /sale/api/deliveries/` or `/sale/api/deliveries/<id>/` | `sale.view_chalan`, `sale.view_delivery` |
| `POST /sale/api/chalans/<id>/delivery/` | Delivery view permissions plus `sale.add_delivery` |
| `PATCH /sale/api/deliveries/<id>/` or `POST /sale/api/deliveries/<id>/status/` | Delivery view permissions plus `sale.change_delivery` |

Assignment accepts `courier` and optional `tracking_number`. Each Chalan has one
delivery, and repeating the same assignment returns the existing record. Change
the courier before handover and update the tracking number as needed. Status
updates accept `status`, optional `note`, and optional `expected_status` to detect
changes made since the page was loaded. Valid updates include handover, transit,
delivery, failed delivery, retry, and return. Couriers are shared across warehouses.
Delivery records follow the Chalan warehouse scope. The delivery list accepts `q`, `page`, and `status`.

Handover deducts the Chalan quantities from its warehouse. While that stock is
with the courier, the warehouse, products, units, and quantities are locked.
Returning a delivery restores the stock deducted by its recorded handover.
When marking a delivery `delivered`, send `payment_account` for a positive payment.
The Chalan total is converted at its saved rate and posted to the account in the
base currency. A zero-value Chalan can be delivered without a ledger entry.
Each posting and its status update commit together. Earlier status records remain
historical, and returns only restore stock deducted by this workflow.

## Point of sale

Open **Sales → Point of sale** at `/sale/pos/`. Cashiers need warehouse access and the
`view_salesorder`, `view_salesorderitem`, `add_salesorder`, `add_salesorderitem`, and `change_salesorder`
permissions. Quick customer creation uses the existing People add permission.

The catalog lists active products in the base currency with positive stock at the selected warehouse.
Mark products **Featured in POS** on the inventory form. Scanning matches an exact barcode first, then an exact SKU.
The checkout uses the product price or its promotion for the transaction date. Decimal quantities use the product's own unit.
The server quotes line discounts, then allocates the order discount and coupon before calculating tax.
Product tax rates apply unless the cashier supplies an override. POS line amounts and tenders round to the currency cent.

Cash needs an active cash account available to the sale warehouse. Card and cheque need a bank account and
a reference for the externally accepted payment. Gift cards redeem their existing balance. For customer deposits,
issue a **Customer deposit account** under **Sales → Gift cards**, choose the customer, and enter the amount
already funded. Recharge that account for later deposits. At the till, select the customer to use their balance.
Both stored-value tenders release the original liability. A return restores stored value first, then refunds
the original collection accounts. Shipping is excluded from merchandise returns.

| Route under `/sale/api/pos/` | Behavior |
| --- | --- |
| `GET catalog/`, `GET customers/` | Search available products or eligible customers. Catalog accepts warehouse, date, scan, q, category, brand, featured and page. |
| `POST quote/` | Send warehouse, date, optional customer, items and adjustments. Returns server prices, totals and the quote fingerprint. |
| `POST draft/` | Send a cart, UUID key and revision. Saves the cashier's cart without reserving stock or consuming funds. |
| `POST checkout/` | Send the same cart, key, revision, quote fingerprint and tender allocations. Retry the original request after a timeout. |
| `GET history/` | Show this cashier's recent tickets at the requested warehouse. |
| `GET tickets/<uuid>/`, `POST tickets/<uuid>/` | Resume a draft or reopen its receipt. POST the current revision to discard a draft. |

Checkout commits the sale, stock deduction, coupon use, tender postings and receipt together.
A changed quote or conflicting ticket revision returns 409 so the cashier can review it before paying.

## Coupons and gift cards

Open **Sales → Coupons** or **Sales → Gift cards**. Each menu uses its corresponding sale model's view permission.
Issuing requires add permission. Recharging, changing expiry and deactivating require change permission.
The programs are shared across warehouses. A cashier with warehouse access and `sale.add_salesorder` can validate a presented code without access to the management list.

| Endpoint | Behavior |
| --- | --- |
| `POST /common/api/codes/generate/` | Send `{"purpose":"coupon"}` or `{"purpose":"gift_card"}` to receive a candidate in `code`. The database enforces uniqueness at issue. Other apps can register a model, field, length and alphabet in `common/codes.py`. |
| `GET/POST /sale/api/coupons/` | Create with `kind` (flat or percentage), `value`, `quantity` and optional `code`. One code has one shared use limit. Flat amounts use the base currency. |
| `POST /sale/api/coupons/validate/` | Send `code` and eligible `subtotal` for a preview. Validation leaves the quantity available. |
| `GET/POST /sale/api/gift-cards/` | Issue with `amount`, `expired_date`, optional 16-digit `card_no`, and optionally one of `customer_id` or `user_id`. Omit both owners to issue a bearer card. |
| `POST /sale/api/gift-cards/<id>/recharge/` | Send a positive `amount`. Send a stable UUID `request_key` on issue and recharge to make retries safe. |
| `POST /sale/api/gift-cards/<id>/expiry/` | Send the new `expired_date` as YYYY-MM-DD. The card is valid through that date. |
| `POST /sale/api/gift-cards/validate/` | Send `card_no` and, for customer-owned cards, `customer_id`. Staff-owned cards are checked against the authenticated user. Owner checks default on via `POS_STRICT_GIFT_CARD_OWNER`. |
| `POST /sale/api/{coupons,gift-cards}/deactivate/` | Send an `ids` array. Deactivating used gift cards is rejected. Financial history is retained. |
| `GET /sale/gift-cards/<id>/card.png?download=1` | Download the visible card with its current balance and expiration date. |

List endpoints accept `page`, `per_page` (10, 25, 50, all), `search`, `sort_by` and `sort_dir`.
Include `payable_amount` in gift-card validation to preview `tender_amount` and `remaining_due` for split payment.
Gift-card totals cover the filtered list. Exports include all columns and use the selected rows, or the entire current page when no rows are selected.

POS checkout consumes coupons and stored-value tenders inside sale completion. Issue, recharge, redemption
and refund each record a corresponding liability movement in accounting.

Base path: `/sale/api/`. Session authentication and Django model permissions are
required. Writes require a valid session cookie and `X-CSRFToken`. JSON, form
and multipart parsers match the existing base APIs.

| Resource | List/create | Retrieve/update/delete |
| --- | --- | --- |
| Orders | GET/POST `/sales-orders/` | GET/PUT/PATCH/DELETE `/sales-orders/<id>/` |
| Items | GET/POST `/sales-order-items/` | GET/PUT/PATCH/DELETE `/sales-order-items/<id>/` |

Both resources also have GET-only `<resource>/list/` and dedicated PUT/PATCH
`<resource>/<id>/update/` routes. URL namespace: `sale`.

GET requires `sale.view_salesorder` or `sale.view_salesorderitem`, POST requires
the corresponding `add` permission, PUT/PATCH `change`, and DELETE `delete`.
Group grants work normally. Dedicated updates need change permission without
view permission; HEAD/OPTIONS use view permission except on update-only routes.

## Create an order

POST `/sale/api/sales-orders/`:

```json
{
  "customer": 1,
  "warehouse": 1,
  "payment_account": 2,
  "currency": "BDT",
  "exchange_rate": "1.00000000",
  "customer_reference": "CUSTOMER-PO-100",
  "billing_address": "Agreed billing address",
  "shipping_address": "Agreed delivery address",
  "shipping_cost": "5.0000",
  "items": [
    {
      "line_number": 1,
      "product": 7,
      "unit": 1,
      "quantity": "3.000000",
      "unit_price": "10.0000",
      "discount_amount": "2.0000",
      "tax_rate": "15.0000",
      "description": "Keyboard"
    }
  ]
}
```

Replace IDs with real records. Customer and warehouse are optional and accept
null. `payment_account` is required and must refer to an active base-currency
account for the sale warehouse or a shared account within the user's scope.
Customer must have is_customer=True and must not be soft-deleted. Currency
uses an existing code. Exchange rate is positive and must be 1 for the base
currency. Order date defaults to today; optional expected_delivery_date cannot
precede it. The unit must match the product. Quantity and line numbers must be
positive; discount cannot exceed quantity times price; tax is between 0 and 100.

Success returns 201 with `message` and `sales_order`. Order number, audit fields,
confirmed_at, fulfilled_at, customer/warehouse labels and calculated
subtotal/tax_total/grand_total are read-only. Status accepts an existing
TradeStatus code, such as `"status": "confirmed"`; omitting it defaults to draft.
Unknown codes and product-only statuses return 400.
For the example, subtotal is 28, tax is 4.2 and grand total is 37.2.

Nested creation requires add permission for both orders and items. Everything
is saved in one transaction. Omitting items permits header-only API creation;
when provided, items must be nonempty and cannot contain existing IDs.

## Import a sale from CSV

Open **Sales list → Import CSV** to download a template and upload the product
lines for one sale. `GET /sale/api/sales-orders/import/` downloads the template.
`POST /sale/api/sales-orders/import/` accepts `multipart/form-data` with `file`,
`currency`, `exchange_rate`, `payment_account`, and any optional order header
fields from the create API. `GET /sale/api/sales-orders/import/options/` returns
the header choices for the import form. These routes use the same order and item
add permissions and warehouse scope as regular sales creation.

The UTF-8 CSV requires `sku` and `quantity`. Optional columns are `unit_price`,
`discount_amount`, `tax_rate`, and `description`. Each SKU must match an existing
product. A blank price uses the catalog price when the currencies match. Supply
a price in the sale currency when they differ. A blank tax uses the catalog tax
rate, and a blank discount uses zero. Each line uses its product unit, and the
sale preserves file order. The limit is 1,000 product rows and 5 MB.

An invalid file returns 400 with errors under `file`, including row numbers for
invalid lines. A successful import returns 201 with `sales_order`. The order
starts uncompleted. Completing it applies the stock deduction and payment.

## Update and delete

PATCH accepts changed header fields. Include `items` to provide the complete
desired line list: retain an existing line by including its `id`, add a line by
omitting `id`, and remove an existing line by excluding it from the array.
Line IDs must belong to this order and cannot repeat. Line numbers must be unique.
Optional line discount, tax and description default to zero/blank in that full
representation. Unchanged lines need no item mutation permission; adding,
changing or removing lines requires the corresponding item permission. Omitting
items leaves lines unchanged. An empty items array is rejected.

Updates preserve existing item IDs and creation audit fields, including when
lines are renumbered. Header and line changes roll back together on failure.
Individual item APIs remain available; existing items cannot move to another
order. DELETE of an order with lines requires both order and item delete
permissions and removes both atomically. Deletion is permanent.

Uncompleted orders and items remain editable/deletable at any trade status with
the required permissions. PATCH `{"status": "confirmed"}` changes the status
label only. Completed orders and their items reject updates/deletion with 409.

## Complete a sale and post payment

Warehouse scope and model permissions come from the user's single role for all order/item APIs,
including this completion action. Specific-warehouse users cannot read, change,
delete or complete another warehouse's sale. Missing warehouse on creation
defaults to their assignment; explicit null/other warehouses are rejected.
Options show only their active assigned warehouse and the form locks the
dropdown. Missing assignments deny access. See
[warehouse scope rules](../warehouse/API.md#warehouse-scope-enforcement).

POST `/sale/api/sales-orders/complete/`:

```json
{"sale_id": 7, "is_completed": true}
```

Requires `sale.change_salesorder`, session authentication and CSRF. An active
warehouse and at least one item are required. Quantities are combined by product
ID and deducted from that warehouse's existing Stock balances. Missing or
insufficient stock returns 400 and rolls back all changes. Stock cannot go negative.

Completion credits the outstanding total to the order's `payment_account` as a
sales payment. Foreign-currency totals are converted with the saved exchange
rate. Stock deduction, payment posting and completion are committed together.
Assign a payment account to an older open sale before completing it.

Success returns 200 with `message`, `completed_now` and `sales_order`.
The sale becomes `is_completed=true`, `fulfilled_at` records the completion time,
and its header/items become read-only, including deletion and new lines.
Repeating the request returns `completed_now=false` and keeps the original stock
deduction and payment.
Concurrent stock updates may return a retryable 409; retrying is safe.
TradeStatus is preserved. Completion is independent of the status label.
The completion flag and timestamps cannot be written through ordinary CRUD APIs.

## Lists, options and pages

Lists are paginated at 20 rows; use `page` and `q`. Orders sort newest first;
items sort by order, line number and ID. Order search includes customer
name/code, order number, reference and notes. Item search includes product
name/SKU, order number and description. Filter items with `?sales_order=<id>`.

GET `/sales-orders/options/` requires both order/item add permissions. It returns
customer, warehouse, currency, status, product and `payment_account` choices.
Payment-account choices include their warehouse IDs. Status choices contain
all TradeStatus records. Products include unit, currency, sale price, minimum_sale_price (decimal string or null) and tax defaults; the UI
prefills prices only when currencies match and requires manual prices otherwise.
Create/edit lines show the minimum sale price read-only in the product currency,
or Not set when no minimum is configured.
Customer addresses can prefill editable order address snapshots. Party/catalog
view permissions are not required just to populate an authorized sales form.
GET `/sales-orders/edit-options/` requires order view/change and item view.

Web routes (namespace `sale_pages`):

- `/sale/sales-orders/`: list, search, pagination and view/edit/delete/complete row icons.
- `/sale/sales-orders/add/`: create an order with products; success returns to list.
- `/sale/sales-orders/<id>/`: order details, totals and paginated items.
- `/sale/sales-orders/<id>/edit/`: edit header and all lines; success returns to list.

Create pages require order view/add and item add permissions. Edit pages require
order view/change plus item view/add/change/delete because the editor supports
all line operations. Detail pages show lines only with item view permission.
The Complete sale action requires change permission, is disabled after success,
and completed rows keep edit/delete visible but disabled. Direct edit pages also enforce read-only
behavior, including when another request completes a sale while it is being edited.
The editor loads every item page before enabling save, retains agreed prices,
and preserves unchanged historical customer links even after Party soft deletion.
Deleted customers cannot be selected for new assignments.

Validation errors return 400, permission failures 403, missing records 404 and
locked orders 409.

## Sales returns

Open **Sales > Sales returns**, or choose **Create return** from a completed
sale. Choose return quantities against the original sale lines, then save the
draft. Completion restores stock to the original sale warehouse, posts the
refund, and locks the return in one transaction. Remaining quantity is calculated per
sale line using completed returns.

The refund covers the returned merchandise at its original discounted price
with proportional tax and the exchange rate saved on the sale. Shipping is
excluded from the merchandise refund. If a sale has several original payment
accounts, each can refund up to the amount still collected through that
account. An unfunded refund account rolls back both the refund and the return.
The return detail response exposes the posted `refund_amount` and `currency`.
Account statements show each refund as a sales refund outflow with the return
number as its reference.

| Method and path under `/sale/api/` | Purpose |
| --- | --- |
| `GET sales-returns/` or `sales-returns/list/` | Paginated, searchable list |
| `POST sales-returns/` | Create a draft with `sales_order`, optional `return_date` and `notes`, and `items` containing `sales_order_item` and `quantity` |
| `GET/PATCH/DELETE sales-returns/<id>/` | Read, edit, or delete a draft. Read completed records here as well |
| `PUT/PATCH sales-returns/<id>/update/` | Update a draft. An `items` payload replaces the draft lines |
| `GET sales-returns/options/?sales_order=<id>` | Returnable quantities for a completed sale. Omit the query parameter to list eligible sales |
| `POST sales-returns/complete/` | Send `return_id` and `is_completed: true` to complete the return |

Return permissions use the existing sales role and warehouse scope. Creating a
draft requires `add_salesreturn`, `add_salesreturnitem`, `view_salesorder`, and
`view_salesorderitem`. View line details with `view_salesreturnitem`. Completing
a draft requires `change_salesreturn`. A completion retry returns
`completed_now: false` after the first successful completion.

## Sales quotations

Open **Sales → Quotations** to create a draft for a customer and warehouse.
The default validity date is 30 days after the quotation date. It is editable
on drafts. Quotation prices, discounts, tax, and shipping use the sales order
calculations. Terms appear on the customer PDF. Internal notes stay on the
quotation.

| Method and path under `/sale/api/` | Purpose |
| --- | --- |
| `GET/POST quotations/` | List or create drafts. Creation takes nested `items` |
| `GET/PATCH/PUT/DELETE quotations/<id>/` | Read any visible quotation or modify a draft |
| `GET quotations/options/` or `quotations/edit-options/` | Get customer, warehouse, currency, and product choices |
| `POST quotations/<id>/issue/` | Lock the draft and save the PDF snapshot |
| `POST quotations/<id>/accept/` or `decline/` | Record a response through the validity date |
| `POST quotations/<id>/revise/` | Copy the terms and lines into a fresh draft and supersede an issued quote |
| `POST quotations/<id>/convert/` | Create one linked draft sales order from an accepted quote using the supplied `payment_account` |

Send `{}` to the workflow endpoints. The response includes `sales_quotation`
and `quotation_url`. Retrying conversion returns the already linked order.
Quotes accepted by their validity date can be converted later. Stock changes
only when the linked sales order is completed through the sales workflow.

The API follows sales role permissions and warehouse scope. Issuing, recording
responses, and converting require `issue_salesquotation`, `respond_salesquotation`,
and `convert_salesquotation` respectively, along with quotation and line view
permissions. Conversion also requires order and order-line add permissions.
The `report:quotation-pdf` route requires both quotation and quotation-line
view permissions.
