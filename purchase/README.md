# Purchase models

PurchaseOrder and PurchaseOrderItem inherit common.models.TimeStampedModel.
PurchaseOrder also has an optional receiving warehouse and server-controlled
is_received=False flag. Set the warehouse before receiving. POST
`/purchase/api/purchase-orders/receive/` with purchase_id and is_received=true
receives every item in one transaction and permanently locks the purchase and
its items against normal API edits/deletion. Receipt increments one Stock row
per warehouse/product, combining repeated product lines and purchases. Retrying
the same purchase returns its received result without increasing stock again.
The purchase creation page includes the receiving warehouse dropdown; details
show the warehouse and receipt flag.
Supplier is an optional protected foreign key to people.Party (`null=True`,
`blank=True`). New assignments require a non-deleted party with is_supplier=True.
Parties with both roles are eligible; is_active is a separate business status.
Purchase history retains its link when a supplier is soft-deleted or loses the
supplier role, and unrelated partial updates remain possible.

Migration 0002 converts nonblank legacy supplier names to Party references. A
unique exact-name supplier match is reused; otherwise it creates a supplier
with a unique LEGACY-SUP code. It never promotes customer-only or deleted parties
or chooses between namesakes. Repeated legacy names reuse the same new party.
Order audit fields and numbers are preserved. Empty/whitespace-only names become
null. Rollback copies the linked party name back to the text field and retains
imported Party records.

Order numbers default to PO- followed by a UUID hex string, protected by a unique
constraint. Status is a protected foreign key to common.TradeStatus, referencing its
unique code. TradeStatus has its own table and audit fields, independent of
ProductStatus, which products reference directly. Draft, confirmed, closed and cancelled
are seeded records, not hard-coded choices. New statuses need no schema change.
Dates, positive exchange rates, nonnegative shipping, and valid status references
have database constraints.

Item quantities have six decimal places, prices/discounts four, and tax percentages
four. Line numbers are unique within an order. Quantity and line number must be
positive; prices/discounts cannot be negative, discounts cannot exceed the gross
line amount, and tax is between zero and 100. For now, each line uses the product's
unit; unit conversion is not supported.

Pass agreed unit_price, unit, description and exchange_rate when creating records;
these values do not automatically follow future product or currency changes.
Exchange rates mean base-currency units per order-currency unit. Base currency
must have rate 1. An unknown catalog rate must be supplied explicitly before use.

Totals are computed properties, not editable database columns: item.net_amount
is quantity * unit_price - discount_amount; tax_amount applies tax_rate to that
rounded net amount; line_total adds the two. Amounts use Decimal and four-place
ROUND_HALF_UP rounding. Order subtotal and tax_total sum item amounts; grand_total
adds shipping_cost. Prefetch items when calculating totals for multiple orders.

Use full_clean() before programmatic saves for model-level checks; normal Django
save() does not call it. Currency, status, product, unit, and order relationships
are protected against deletion when referenced.

CRUD APIs are available under `/purchase/api/`; see [API.md](API.md) for routes,
permissions and request examples. The purchase list and creation pages are under
`/purchase/purchase-orders/`. The creation page saves an order and all its items
atomically through the order API; individual item APIs remain available.
Unreceived purchases have permission-controlled edit actions on the list. The
edit page reuses the create form, loads all item pages and saves header/line
changes atomically through PATCH, then returns to the list. Received purchases
keep edit and delete actions visible but disabled and remain locked by the API.
The list delete action requires order and item delete permissions and removes
an unreceived purchase and its lines atomically. Nested edits preserve existing
line IDs and creation metadata and enforce each item action's permissions.
Creating or editing an unreceived order does not change stock. The receive
service is the only purchase action that increases stock. It does not change
the trade status, create a movement ledger, or support partial receipts. Received
order/item writes use a shared parent guard. Programmatic callers should use
these serializers/services; direct ORM or SQL writes bypass the API workflow.
