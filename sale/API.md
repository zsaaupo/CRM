# Sales APIs and pages

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
draft. Completion restores stock to the original sale warehouse and locks the
return. A draft leaves stock unchanged. Remaining quantity is calculated per
sale line using completed returns.

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
