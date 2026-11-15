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

## Add stock

- POST `/warehouse/api/stocks/` creates a Stock row. Returns 201 with `message`
  and `stock` keys; audit fields are server-controlled.
- GET `/warehouse/api/stocks/options/` returns `warehouse` and `product` arrays
  containing only `value` and `label`; product labels include SKU and unit code.
- `/warehouse/stocks/add/` renders the Add stock page. Its dropdowns load through
  the options API; saving stays disabled until required choices load successfully.
- The page and both APIs require `warehouse.add_stock`, including group grants.
  The options API does not require warehouse/product management permissions.
- Each warehouse list row has an Add stock plus icon for users with
  `warehouse.add_stock`. It opens `/warehouse/stocks/add/?warehouse=<id>` and
  preselects that warehouse after the options API loads. The warehouse dropdown
  is disabled, but its ID is included in the API submission. There is no Add stock
  sidebar menu entry. A missing or invalid warehouse keeps saving disabled and
  asks the user to return to the list and choose a valid warehouse.
- Successful creation redirects to the warehouse list if the user has
  `warehouse.view_warehouse`; otherwise it redirects to the dashboard.

```json
{
  "warehouse": 1,
  "product": 2,
  "quantity": "12.123456"
}
```

Warehouse and product are numeric IDs and must exist. Quantity is nonnegative,
supports six decimal places, and defaults to zero when omitted. Duplicate
warehouse/product pairs return 400; this endpoint creates a balance and does not
increment or overwrite an existing balance. Writes require session cookies and
CSRF. Only POST/OPTIONS are supported by the creation endpoint. Options are
read-only (GET/HEAD/OPTIONS). All responses are marked no-store.

## Stock list and details

Stock navigation, pages and read APIs require `warehouse.view_stock` (direct or
through a role). Warehouse/product management permissions are not required to
read stock labels and quantities. Anonymous page requests redirect to login;
unauthorized API/page requests return 403.

| URL | Methods | Purpose |
| --- | --- | --- |
| `/warehouse/api/stocks/list/` | GET, HEAD, OPTIONS | Read stock balances |
| `/warehouse/api/stocks/<id>/` | GET, HEAD, OPTIONS | Read one stock record |
| `/warehouse/stocks/` | GET | Stock list page |
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
