# Sales order management

The `sale` app contains SalesOrder and SalesOrderItem, their CRUD APIs and
API-driven list, create, detail and edit pages. The sidebar has Sales list and
Create sale links. See [API.md](API.md) for requests and permissions.

Sales orders have generated SO- references, optional Party customers and
warehouses, delivery dates, currency/exchange-rate snapshots, address snapshots,
shipping costs and audit fields. New customer assignments require a non-deleted
Party with is_customer=True; a party with both roles is eligible. Inactive
customers remain eligible, matching the existing purchase supplier policy.
Soft-deleted customers remain linked to existing order history.

SalesOrder.status references common.TradeStatus, a proxy subclass of
common.ProductStatus with no added fields or table. Products use ProductStatus;
purchase and sales orders use TradeStatus. All share existing status codes/rows.

Quantities use six decimal places; prices, discounts, tax rates and calculated
amounts use four. Discounts apply to the whole line. Units must match products.
Totals use Decimal and ROUND_HALF_UP, matching purchase orders. Later product
price, tax or Party address changes do not rewrite saved line values or addresses.

This version manages draft orders. Status and confirmation/fulfillment timestamps
are read-only in the APIs. Orders and their items cannot be changed or deleted
through the APIs when the order is not draft. Order edits and nested item edits
are atomic; line IDs and original creation audit fields are retained. All item
writes lock their parent order. Draft deletion removes its lines and then the
order in one transaction, checking both deletion permissions.

Creating, updating and deleting sales records never changes Stock. StockMovement,
fulfillment/confirmation actions, invoices, payments, returns and refunds remain
future work. The warehouse field records a planned source only. There is no
inventory reservation or availability enforcement in order entry yet.

Programmatic callers must run full_clean() for model validation. Use the API
serializers/services for transactional draft-only writes; normal Django save()
does not automatically validate a model or enforce the API workflow.
