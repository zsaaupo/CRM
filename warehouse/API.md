# Warehouse APIs

Warehouse APIs reuse the generic views in `common/view.py` and the shared
serializer validation in `common/serializers.py`.
All endpoints require SessionAuthentication, IsAuthenticated, and
HasRequiredPermissions. Writes require the current X-CSRFToken header.

| URL | Methods | Purpose |
| --- | --- | --- |
| `/warehouse/api/warehouses/` | GET, POST | List and create |
| `/warehouse/api/warehouses/list/` | GET | Read-only list |
| `/warehouse/api/warehouses/<id>/` | GET, PUT, PATCH, DELETE | Retrieve, update, hard delete |
| `/warehouse/api/warehouses/<id>/update/` | PUT, PATCH | Dedicated update |

Lists return count, next, previous, and results (20 per page), ordered by name
then ID. Use `?q=term` to search name, code, address, phone, or email, and
`?page=2` for pagination. Read endpoints also support HEAD and OPTIONS.
The dedicated update endpoint supports OPTIONS with change permission.

Permissions are `warehouse.view_warehouse` for reads,
`warehouse.add_warehouse` for POST, `warehouse.change_warehouse` for PUT/PATCH,
and `warehouse.delete_warehouse` for DELETE. Group grants work normally.
Staff status alone does not grant API access. Responses are never cached.

POST example:

```json
{
  "name": "Main warehouse",
  "address": "Dhaka, Bangladesh",
  "phone": "01700000000",
  "email": "warehouse@example.com",
  "code": "WH-001",
  "is_active": true
}
```

Name, address, and phone are required. Email and code may be omitted, empty, or
null; code follows the current model and is not unique. Each string has a maximum
length of 200. Creates return 201 with `message` and `warehouse` keys. Updates
return 200 with the saved object. PATCH accepts selected fields; PUT must include
required fields. Deletion returns 204 with no body and permanently removes the
record. Protected references return 409. Missing records return 404; invalid
data returns 400; missing session/permissions/CSRF return 403.

ID and audit fields are read-only. The server fills create_by and modified_by
from the signed-in username and preserves creation metadata during updates.
JSON, form-encoded, and multipart requests are accepted, matching inventory APIs.

## Warehouse scope enforcement

Warehouse data APIs require HasWarehouseAccess in addition to IsAuthenticated
and existing model CRUD permission checks using the assigned role's grants.
Direct user permission grants do not bypass the role for warehouse features. A role's specific assignment limits
warehouses, stock, purchase orders/items and sales orders/items to that warehouse.
All warehouses permits every warehouse and existing warehouse-less drafts.
Superusers are unrestricted. Missing/unassigned configurations deny access with
403, even when the user has CRUD permissions. Existing users must be assigned
through the assignment APIs before using these features; no automatic grants
are made during rollout.

Scope is applied before search, counts, pagination and object lookup. An
out-of-scope detail/update/delete ID returns 404. Creates default a missing
warehouse to the user's specific assignment; an explicit null or another
warehouse is rejected with 403. Updates cannot move records outside that scope.
Standalone item APIs inherit scope from their parent orders and validate parent
references against scoped querysets. Unknown/out-of-scope parent IDs return 400.

Purchase/sales options contain only allowed active warehouses and include
`warehouse_scope: {"warehouse": 2, "all_warehouses": false}` for a restricted
user. Forms select and disable that warehouse; the backend enforces the same
rule on direct API calls. All-warehouse users can choose a warehouse. Shared
products, parties, currencies and statuses keep their existing permissions.
Warehouse creation requires all-warehouse access plus add_warehouse permission.

Receipt and completion services recheck scope inside their stock transaction,
including repeat requests. Order/item edits and deletes recheck scope under
the same assignment lock. Assignment changes take effect on the next request
without logging in again. Received/completed records retain their existing
read-only rules. Pages/navigation and warehouse/stock Django admin queries and
warehouse choices follow the same scope. All-warehouse access grants no CRUD
permissions by itself.


## Role warehouse access assignments

Warehouse scope belongs to a Django Group (role), not a user. Each role can have
one RoleWarehouseAccess configuration. Every user assigned that role inherits
its warehouse or All warehouses setting. The existing account role-assignment
APIs continue to assign one group to each user.

| URL | Methods | Purpose |
| --- | --- | --- |
| /warehouse/api/role-warehouse-access/ | GET, POST | List and create role assignments |
| /warehouse/api/role-warehouse-access/list/ | GET | Read-only assignment list |
| /warehouse/api/role-warehouse-access/options/?role=<id> | GET | Current scope and allowed warehouse choices for the role permission page |
| /warehouse/api/role-warehouse-access/<id>/ | GET, PUT, PATCH, DELETE | Read, update or delete |
| /warehouse/api/role-warehouse-access/<id>/update/ | PUT, PATCH | Dedicated update |

The URL ID is the configuration ID, not the role ID. Search ?q=term matches role
names and warehouse names/codes. Filters are ?role=<id>, ?warehouse=<id> and
?all_warehouses=true or false. Lists use 20-row pagination, ordered by role ID.
Read endpoints support HEAD/OPTIONS; dedicated updates support OPTIONS.

Assignment management requires warehouse.manage_warehouse_access inherited from
the acting user's role. In Roles → Manage permissions, check Manage warehouse
assignments in the Additional permissions column for rolewarehouseaccess and
save the role permissions. That single checkbox authorizes assignment reads,
create, update and delete; the old assignment model CRUD grants are not required
and do not authorize management on their own. Sales/purchase/stock CRUD grants
remain separate. Unchecking the permission revokes management on the next request.

Members still need a valid role warehouse scope, and existing own-role,
superuser-role and warehouse boundaries remain enforced. Management does not
allow changing one's own scope or granting beyond the actor's allowed warehouses.
Session authentication and CSRF for writes are required.

POST /warehouse/api/role-warehouse-access/:

```json
{"role": 7, "warehouse": 2, "all_warehouses": false}
```

Success returns 201 with message and role_warehouse_access. Records contain id,
role, role_name, warehouse, warehouse_name, warehouse_code, all_warehouses and
server-controlled audit fields. Role cannot be changed after creation.

PATCH to switch to all warehouses:

```json
{"warehouse": null, "all_warehouses": true}
```

Both settings may be cleared to deny warehouse access to the role's members.
A warehouse plus all_warehouses=true is invalid. Invalid/duplicate roles and
invalid warehouse IDs return 400. DELETE returns 204 and removes only the
configuration, preserving the role, members, warehouses and stock. Its members
then lose warehouse access.

Superusers can configure any role without having a role themselves. Other
managers need an assigned role with warehouse access. They cannot change or
delete their own role's scope, or manage a role containing a superuser. A
specific-warehouse manager can manage only assignments within that warehouse
or unassigned configurations. Only an all-warehouse manager or superuser can
grant All warehouses. These limits also apply when assigning a warehouse-enabled
role to a user through the account APIs.

No role, multiple roles, or a role without configured warehouse access denies
warehouse data access. Changing a role's scope applies to all its members on the
next request. Changing a user's role changes their inherited access immediately.
Old user-warehouse-access routes are removed. Migration converts compatible
user configurations to roles and preserves CRUD permission grants; it refuses
conflicting member scopes rather than silently broadening access.

The existing role permission page includes a Warehouse access section with
No warehouse access, Specific warehouse and All warehouses choices. It loads
the current configuration and choices from the options endpoint. One Save permissions
button sends both the selected permission IDs and warehouse_access to the existing
account group-permissions PUT endpoint. Both settings save in one database
transaction; any failure rolls back both. Success returns to the role list.
Reset selections restores both sections. Users without assignment-management
permission can continue saving model permissions without changing warehouse scope.

The options endpoint requires manage_warehouse_access from the actor's role;
it does not require warehouse catalog view permission. It returns role,
assignment (or null), warehouse choices, can_assign_all, can_create and can_change.
Choices and capabilities respect the actor's warehouse scope. The page disables
editing when management permission is unavailable, and backend checks remain
authoritative. Missing warehouse-assignment management permission leaves the original
CRUD permission editor available with an explanatory message for the scope section.

## Web pages

The sidebar includes a Warehouses section for users with warehouse or stock view
permission. Warehouse links require warehouse view permission; Stock list requires
stock view permission. Add warehouse is shown when its add permission is granted.

| URL | Page | Required permissions |
| --- | --- | --- |
| `/warehouse/warehouses/` | List | view_warehouse |
| `/warehouse/warehouses/add/` | Add | view_warehouse, add_warehouse |
| `/warehouse/warehouses/<id>/edit/` | Edit | view_warehouse, change_warehouse |

All permission names above use the `warehouse.` prefix. Pages reuse the inventory
HTML field/list templates, styling, API JavaScript, search, and pagination. They
do not use Django forms or load warehouse records in page views. Add submits
POST, edit loads GET detail and saves PATCH to the dedicated update endpoint,
and both redirect to the warehouse list after success. Red delete actions use
the existing detail API with confirmation and refresh the list after deletion.
Buttons are hidden without their action permission; APIs enforce permissions
and CSRF independently. An absent edit record is reported by the detail API.

## Stock changes

Manual stock creation through the API or dashboard is unavailable. Purchase
receipts create or increase warehouse/product balances; completing a sale
decreases existing balances. Existing stock data is preserved.

## Stock list and details

Stock navigation, pages and read APIs require `warehouse.view_stock` (direct or
through a role). Warehouse/product management permissions are not required to
read stock labels and quantities. Anonymous page requests redirect to login;
unauthorized API/page requests return 403.

| URL | Methods | Purpose |
| --- | --- | --- |
| `/warehouse/api/stocks/list/` | GET, HEAD, OPTIONS | Read stock balances |
| `/warehouse/api/stocks/chart/` | GET, HEAD, OPTIONS | Stock value summaries by product category |
| `/warehouse/api/stocks/<id>/` | GET, HEAD, OPTIONS | Read one stock record |
| `/warehouse/stocks/` | GET | Stock list page |
| `/warehouse/stocks/chart/` | GET | Stock chart page |
| `/warehouse/stocks/<id>/` | GET | Stock details page |

The list reuses the current table design, search and 20-row pagination. It shows
warehouse, product SKU/name, quantity and unit. Each row has only the eye icon
linking to its stock detail page, including for superusers. No add, edit, delete,
receive or stock-operation actions are shown on this list. Details reuse the
shared record-detail loader, loading/error/retry UI, audit section and dashboard
layout, with a link back to Stock list.

List responses use count/next/previous/results, ordered by warehouse name,
product name and ID. Search `?q=term` matches warehouse name/code and product
name/SKU. Optional `?warehouse=<id>` and `?product=<id>` filters accept positive
IDs; invalid filters return 400. Missing detail records return 404. Read APIs
reject POST/PUT/PATCH/DELETE with 405 and never cache responses.

Read records include the stock ID, warehouse/product IDs, warehouse name/code,
product name/SKU, six-decimal quantity, unit name/code and audit fields. Related
labels are loaded in the stock query. Purchase receipts update the existing
Stock balance, so the next list/detail request shows the new quantity.

The stock chart API also requires `warehouse.view_stock` and the user's
warehouse scope. Without `?warehouse=<id>`, it returns a specific-warehouse
role's assigned branch or aggregates all branches for an all-warehouse role.
All-warehouse users may select one branch with that filter; out-of-scope
branches return 404 and invalid IDs return 400.

The response contains base-currency totals for stock at catalog selling price
(`quantity × sale_price`), estimated cost (`quantity × cost_price`), and
estimated profit (selling value minus cost). Product currency exchange rates
convert values to `INVENTORY_BASE_CURRENCY`. Stock in currencies
without a configured rate is excluded and listed in
`missing_exchange_rate_currencies`. These are current catalog-price estimates,
not FIFO or weighted-average valuations of received purchase costs. Estimated
profit excludes tax and operating costs. `total_items` counts distinct products
with positive stock; `total_quantity` adds their quantities, and
`quantity_unit_count` reports how many different product units are represented.
The page shows total items and quantity above one pie with slices for stock value
by price, stock value by cost and estimated profit. Since a pie cannot represent
a zero or negative profit slice, it is omitted and called out in a warning.
All-warehouse users can switch between All branches and individual branches in
the page dropdown.

Warehouse assignments are configured from Roles → Manage permissions. There is
no separate warehouse-assignment navbar entry or page. The options endpoint
requires a role parameter.

## Warehouse transfers

Each transfer contains multiple product lines moving from one source warehouse to
one destination. Each line records its product, unit and quantity (six decimal
places). Creating, editing
or deleting a draft never changes stock. Completed transfers cannot be edited or
deleted, including through the dedicated update endpoint.

| URL | Methods | Purpose |
| --- | --- | --- |
| `/warehouse/api/transfers/` | GET, POST | List and create drafts |
| `/warehouse/api/transfers/list/` | GET | Paginated read-only list |
| `/warehouse/api/transfers/<id>/` | GET, PUT, PATCH, DELETE | Read, edit or delete a draft |
| `/warehouse/api/transfers/<id>/update/` | PUT, PATCH | Update a draft |
| `/warehouse/api/transfers/options/` | GET | Creation choices; requires add permission |
| `/warehouse/api/transfers/edit-options/` | GET | Editing choices; requires view/change permissions |
| `/warehouse/api/transfers/complete/` | POST | Move stock once and lock the transfer |

Create example:

```json
{"source_warehouse": 1, "destination_warehouse": 2, "notes": "Branch replenishment",
 "items": [{"line_number": 1, "product": 7, "unit": 1, "quantity": "2.500000"},
           {"line_number": 2, "product": 8, "unit": 2, "quantity": "3.000000"}]}
```

The transfer date defaults to today. Transfer numbers and audit values are
server-controlled; sending `is_completed` through draft CRUD cannot complete it.
Create returns a `warehouse_transfer` object; updates return the saved object.

Complete example:

```json
{"transfer_id": 7, "is_completed": true}
```

Success returns `completed_now` and `warehouse_transfer`. The source balance is
decreased and the destination balance increased in one transaction. A missing
destination balance is created; existing balances are reused. Both warehouses
must be active, source stock must cover the quantity, and destination stock must
fit the quantity limit. Any failure rolls back both balances and the completion
flag. Repeating a successful request returns `completed_now: false` without moving
stock again. Concurrent completions and draft writes serialize with completion.
Validation errors return 400; completed-record edits and busy transactions return
409. A busy response can be retried safely.

CRUD permissions are `warehouse.view_warehousetransfer`,
`warehouse.add_warehousetransfer`, `warehouse.change_warehousetransfer` and
`warehouse.delete_warehousetransfer`, inherited from the user's role. Completion
requires change permission. Authentication, CSRF and valid role warehouse scope
are required. Warehouse writes require access to both the source and destination,
so the current single-warehouse role setting allows writes only for All warehouses
roles and superusers. A specific-warehouse role can read incoming/outgoing
transfers involving its warehouse; unrelated transfer IDs return 404 and are
excluded from search, counts and pagination.

Warehouses → Transfer list and Add transfer use the existing dashboard design.
Lists include view, edit, red delete and Complete transfer icons according to
permissions. Completed rows keep edit/delete/complete visible but disabled.
Create/edit save through the APIs and return to the transfer list. Direct edit
pages and edits racing completion also become read-only. The detail page shows
the transfer, warehouses, product, quantity and audit information through the API.

Multi-product transfers require a nonempty items array on creation. Each line must
use its product unit. GET responses include all items, item_count and a product
summary. PATCH can update only header fields without changing items. When items
is supplied it replaces the complete line selection: include existing line IDs
to retain them, omit IDs for new lines, and omit removed lines. Line numbers and
IDs must be unique; IDs must belong to this transfer. Header CRUD permissions
cover all nested item actions. Completed headers and items remain read-only.
Repeated products are combined before stock checks, and a failure on any line
rolls back every product. The create/edit page supports Add item and Remove item;
details show a product table. Existing single-product transfers are migrated
to one line, preserving audit values and completion state without moving stock.
