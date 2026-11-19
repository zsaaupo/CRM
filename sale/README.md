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

SalesOrder.status references the independent common.TradeStatus table.
Products use ProductStatus; purchase and sales orders share TradeStatus.
Trade status changes do not affect product statuses.

Quantities use six decimal places; prices, discounts, tax rates and calculated
amounts use four. Discounts apply to the whole line. Units must match products.
Totals use Decimal and ROUND_HALF_UP, matching purchase orders. Later product
price, tax or Party address changes do not rewrite saved line values or addresses.

Sales forms and APIs accept any existing TradeStatus code. Omitting status on
creation defaults to draft. Status is a label and does not lock an order or
trigger fulfillment; confirmation/fulfillment timestamps remain read-only in CRUD.
Uncompleted orders and their items can be changed or deleted with the required
permissions at any trade status. Order edits and nested item edits
are atomic; line IDs and original creation audit fields are retained. All item
writes lock their parent order. Order deletion removes its lines and then the
order in one transaction, checking both deletion permissions.

Creating, updating and deleting sales records does not change Stock. The separate
Complete sale action deducts stock once from the selected active warehouse,
sets is_completed and fulfilled_at, then locks the header and items against edits
and deletion. Repeated product lines are combined; shortages roll back the entire
completion. Retries cannot deduct twice. Completed rows keep edit/delete visible
but disabled, along with completion. This follows the purchase receipt flow with a stock decrease.
Sales return drafts reference completed sales and their original line items.
Completing a return restores stock to the original warehouse once. Partial
returns share the sold quantity limit per line, and completed returns are
read-only. See [Sales returns](API.md#sales-returns) for requests and permissions.

Programmatic callers must run full_clean() for model validation. Use the API
serializers/services for transactional writes; normal Django save()
does not automatically validate a model or enforce the API workflow.
