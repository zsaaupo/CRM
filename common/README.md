# Shared status models

`ProductStatus` is the renamed former `Status` model. It retains code, name,
description, is_active and the inherited TimeStampedModel audit fields. Product
references ProductStatus using the unique code.

`TradeStatus(ProductStatus)` is a Django proxy model. It adds no fields or table;
purchase and sales order status foreign keys reference this class using code.
TradeStatus and ProductStatus expose the same rows, so creating or editing a
status through either model changes the shared record. This is inheritance
without separate product/trade status datasets.

Both are registered in Django admin as Product statuses and Trade statuses.
Their codes remain immutable through the existing status API/admin, and draft
cannot be deleted. Inventory status routes remain `/inventory/api/statuses/` and
`/inventory/statuses/`, with ProductStatus model permissions.

Migration 0002 renames the concrete model/table, preserves status IDs, codes,
audit fields and related products/orders, and creates the proxy metadata.
Purchase/sale follow-up migrations point their foreign keys to TradeStatus.
Existing common.*_status permissions become common.*_productstatus, preserving
grants and permission IDs where possible. Existing grants are also copied to
common.*_tradestatus for the proxy admin. Duplicate content types/permissions
are merged without dropping group/user grants. Rollback merges trade grants
back into the old Status permissions before removing proxy metadata.

## Trade status settings

Settings → Trade status setting opens `/settings/trade-statuses/`. The list,
add (`/add/`) and edit (`/<id>/edit/`) pages reuse the product-status table,
fields and JavaScript controllers. Successful saves return to the list. Rows
have edit and delete actions when permitted; codes are locked after creation.

APIs under `/common/api/trade-statuses/` provide GET/POST, GET-only `list/`,
GET/PUT/PATCH/DELETE `<id>/` and PUT/PATCH `<id>/update/`. List responses have
20-row pagination and `q` searches name/code. Create responses contain `message`
and `trade_status`; updates return the record directly. Session authentication,
CSRF and common.view/add/change/delete_tradestatus permissions match the shared
API structure. Pages additionally require view permission to reach their list.

The default draft and statuses referenced by products or orders cannot be
deleted (409). ProductStatus and TradeStatus still share records, so settings
edits are visible through either model. No new schema migration is required.
