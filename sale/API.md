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
null. Customer must have is_customer=True and must not be soft-deleted. Currency
uses an existing code. Exchange rate is positive and must be 1 for the base
currency. Order date defaults to today; optional expected_delivery_date cannot
precede it. The unit must match the product. Quantity and line numbers must be
positive; discount cannot exceed quantity times price; tax is between 0 and 100.

Success returns 201 with `message` and `sales_order`. Order number, audit fields,
status, confirmed_at, fulfilled_at, customer/warehouse labels and calculated
subtotal/tax_total/grand_total are read-only. The order always starts as draft.
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
order. DELETE of a draft order with lines requires both order and item delete
permissions and removes both atomically. Deletion is permanent.

Only draft orders and their items are editable/deletable; other statuses return
409. Status transitions and fulfillment actions are not implemented yet. Stock
is never changed by any endpoint in this version.

## Lists, options and pages

Lists are paginated at 20 rows; use `page` and `q`. Orders sort newest first;
items sort by order, line number and ID. Order search includes customer
name/code, order number, reference and notes. Item search includes product
name/SKU, order number and description. Filter items with `?sales_order=<id>`.

GET `/sales-orders/options/` requires both order/item add permissions. It returns
customer, warehouse, currency, status and product choices. Status choices contain
draft only. Products include unit, currency, sale price and tax defaults; the UI
prefills prices only when currencies match and requires manual prices otherwise.
Customer addresses can prefill editable order address snapshots. Party/catalog
view permissions are not required just to populate an authorized sales form.
GET `/sales-orders/edit-options/` requires order view/change and item view.

Web routes (namespace `sale_pages`):

- `/sale/sales-orders/`: list, search, pagination and view/edit/delete row icons.
- `/sale/sales-orders/add/`: create an order with products; success returns to list.
- `/sale/sales-orders/<id>/`: order details, totals and paginated items.
- `/sale/sales-orders/<id>/edit/`: edit header and all lines; success returns to list.

Create pages require order view/add and item add permissions. Edit pages require
order view/change plus item view/add/change/delete because the editor supports
all line operations. Detail pages show lines only with item view permission.
The editor loads every item page before enabling save, retains agreed prices,
and preserves unchanged historical customer links even after Party soft deletion.
Deleted customers cannot be selected for new assignments.

Validation errors return 400, permission failures 403, missing records 404 and
locked orders 409. No restore, stock, invoice, payment or return endpoints exist
in this version.
