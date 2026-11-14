# Party model and APIs

A Party is one customer, supplier, or both. The model lives in `people.models`
and inherits the shared timestamp and actor fields. `code` and `name` are
required; set at least one of `is_customer` or `is_supplier` to true. Codes are
trimmed and uppercased by the API and model validation, with a database constraint
against duplicate codes regardless of case or surrounding spaces. Names may
repeat. Records default to active and sort by name, then ID.

Optional fields are `contact_person`, `email`, `phone`, `alternate_phone`,
`website`, `tax_number`, `address`, `city`, `state`, `postal_code`, `country` and
`notes`. Optional text fields accept empty strings rather than null. Email and
website formats are validated; phone numbers are strings to preserve `+` and
leading zeroes.

`image` is an optional validated image upload. Files are stored under
`MEDIA_ROOT/people_picture/` (currently `media/people_picture/`) and APIs return
the image URL, or null when no image is set. Use multipart form-data with a file
field named `image` on POST, PUT or PATCH; a filename in JSON is not an upload.
Omitting the field on PATCH keeps the current picture; JSON `{"image": null}`
clears its reference. Soft deletion preserves both the picture reference and
the stored file. Existing Pillow and media settings are reused.

| Route | Methods |
| --- | --- |
| `/people/api/parties/` | GET list, POST create |
| `/people/api/parties/list/` | GET list only |
| `/people/api/parties/<id>/` | GET retrieve, PUT/PATCH update, DELETE |
| `/people/api/parties/<id>/update/` | PUT/PATCH update only |

The URL namespace is `people`. All endpoints require an authenticated session.
Reads require `people.view_party`, creation requires `people.add_party`, updates
require `people.change_party`, and deletion requires `people.delete_party`.
Direct and group grants work through the existing shared permission class.
The dedicated update endpoint needs change permission without view permission.
HEAD/OPTIONS use view permission except on that update endpoint. Writes require
the session cookie and a valid `X-CSRFToken` header.

## Create example

POST `/people/api/parties/` with JSON, form data or multipart data:

```json
{
  "code": "SUP-001",
  "name": "Example Trading Company",
  "is_customer": false,
  "is_supplier": true,
  "contact_person": "Jane Doe",
  "email": "supplier@example.com",
  "phone": "+8801700000000",
  "address": "123 Example Road",
  "city": "Dhaka",
  "country": "Bangladesh",
  "tax_number": "TIN-1001",
  "is_active": true
}
```

Returns 201 with `message` and `party` keys. ID, timestamps, `create_by` and
`modified_by` are read-only; actors are filled from the current user.
Retrieval and update responses return the record directly. PATCH validates
against existing values, so a party cannot lose both roles. DELETE performs
soft deletion and returns 204: the row is preserved with `is_deleted=true`,
`deleted_at` and `deleted_by`. The current user is also recorded as `modified_by`.
These deletion fields are read-only. Deleted parties are excluded from both list
endpoints, retrieval and updates; later requests for a deleted ID return 404.
Sending `is_deleted=false` cannot restore a party through these APIs.
`is_active` remains a separate editable business status; inactive parties are
still readable until deleted. Codes remain reserved after soft deletion.

## Lists and errors

Lists return `count`, `next`, `previous` and `results`, with 20 rows per page.
Use `?page=2` for pagination and `?q=example` to search code, name, contact person,
email, phone numbers, tax number or address fields. Optional boolean filters
are `is_customer`, `is_supplier` and `is_active`:

`/people/api/parties/?is_supplier=true&is_active=true&q=trading`

A party with both roles appears in both customer and supplier searches.
Invalid values return 400 with field errors; unauthenticated or unauthorized
requests return 403, missing records return 404. Lists are never cached.

The model is registered in Django admin with read-only audit and deletion fields.
At `/admin/people/party/`, authorized staff can see both current and deleted
parties. Use the **Is deleted → Yes** sidebar filter to find deleted records,
or search by code, name or contact details. The list displays deletion status,
time and actor. Deleted records can be opened read-only; editing and individual
deletion are disabled even for superusers. This does not expose deleted parties
through the regular site or APIs. Normal Django admin view/change permissions
are still required.

Single-record and bulk admin deletion also soft-delete. `Party.objects` excludes
deleted rows; `Party.all_objects` includes them for internal history queries.
Both instance `delete()` and queryset `delete()` soft-delete, including on
`all_objects`. ORM callers may pass `actor='username'` to record who deleted it.
Repeated ORM deletion preserves the original deletion metadata. There is no
hard-delete API, model method or admin action, and no restore endpoint.

Programmatic ORM callers should run `full_clean()` before `save()` for model validation;
normal Django saves do not automatically call it.

Purchase orders now reference Party through an optional protected `supplier`
foreign key. Only non-deleted parties with `is_supplier=true` can be selected
for new assignments; parties with both roles are eligible. Soft deletion preserves
the party and its purchase history. Admin deletion confirmations do not treat
referencing purchases as records to remove or as blockers to soft deletion.

## People pages

The People sidebar contains Supplier list, Add supplier, Customer list and Add
customer in that order. Lists and details require `people.view_party`; add and
edit pages additionally require `people.add_party` and `people.change_party`
respectively. Row actions show view, edit and delete icons according to those
permissions; deletion requires `people.delete_party` and CSRF.

| Page | Supplier | Customer |
| --- | --- | --- |
| List | `/people/suppliers/` | `/people/customers/` |
| Add | `/people/suppliers/add/` | `/people/customers/add/` |
| Detail | `/people/suppliers/<id>/` | `/people/customers/<id>/` |
| Edit | `/people/suppliers/<id>/edit/` | `/people/customers/<id>/edit/` |

Pages use plain HTML and the existing API form/list controllers. The primary
role is checked and locked on each form; the other role can be selected so one
party appears in both lists. Add uses POST, edit loads GET and saves PATCH,
and successful saves redirect to the matching list. Details load through GET.

Lists use `?is_supplier=true` or `?is_customer=true` on the party list API.
The same optional filters now apply to detail, update and delete endpoints to
prevent a supplier page from targeting a customer-only record and vice versa.
These filters select records, not grant permissions. Generic unfiltered APIs
remain available under the existing Party permissions.

Deletion uses the existing soft-delete API, refreshes the list, and retains the
party row and audit history. The confirmation explains that deleting a party
with both roles removes it from both lists. Inactive parties remain visible.

Supplier and customer add/edit pages include an optional Picture upload. The
shared form controller sends multipart data when uploading a file; editing
without a new file preserves the existing image and displays its current
preview. Both lists show the image beside the name, with the first letter as a
fallback when no picture exists or it cannot load. Detail pages load the image
from the party detail API and hide the picture area when no image is available.
