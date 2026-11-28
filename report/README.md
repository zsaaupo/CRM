# Shared PDF documents

All report selection, permission checks, data preparation, rendering, templates,
fonts, download JavaScript, and preview tooling live in this app.

The single `templates/report/document.html` template handles purchase orders,
sales orders and multi-product warehouse transfers. `catalog.py` declares each
document and its existing role permissions; `services.py` prepares a consistent
snapshot using current warehouse scope. Stock and business-state actions are
never invoked. All document lines are loaded, independently of list pagination.

## Runtime

Install the project's requirements. WeasyPrint is already pinned there.
On Windows, run:

```powershell
python manage.py setup_report_runtime
```

This downloads the official WeasyPrint 70.0 onedir release, verifies its SHA-256
and extracts it into ignored `.runtime/weasyprint/`. No system installation is
changed. The renderer loads these local libraries automatically. An existing
Pango installation can instead be supplied through `REPORT_WEASYPRINT_DLL_DIRECTORIES`
(a list of local directories) or the usual `WEASYPRINT_DLL_DIRECTORIES` environment
variable. On Linux, install the native dependencies described in the official
[WeasyPrint installation guide](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html).

Native libraries are imported lazily: a missing runtime returns a JSON 503 from
report endpoints while the rest of the site remains available.

## Branding and layout

Branded reports use the uploaded image from **Settings → Logo**. The fallback
uses each report's existing branding. `REPORT_COMPANY` in Django settings
accepts `name`, `address`, `phone`, `email`, and a local PNG, JPEG or WebP
`logo_path`. Bundled Noto Sans and
Noto Sans Bengali font faces have their SIL Open Font License texts beside them.
Only local report assets can be fetched during rendering; arbitrary network and
file URLs are disabled. Model text uses Django template escaping.

The renderer uses A4 pages, repeated product-table headings, page numbers,
document references, state labels, notes, signature areas and four-decimal money
values. Quantities retain up to six decimal places. Totals use the existing order
calculation methods; discounts are already included in the subtotal.

## API and access

| Endpoint | Required role permissions |
| --- | --- |
| `GET /report/api/purchases/<id>/pdf/` | purchase.view_purchaseorder, purchase.view_purchaseorderitem |
| `GET /report/api/sales/<id>/pdf/` | sale.view_salesorder, sale.view_salesorderitem |
| `GET /report/api/transfers/<id>/pdf/` | warehouse.view_warehousetransfer |

Session authentication and valid warehouse scope are required. Superusers bypass
role/scope requirements. Purchase/sale records are filtered by their warehouse;
transfer reads allow the assigned warehouse to be either source or destination.
Out-of-scope IDs return 404. Missing permission/scope returns 403. Errors remain
JSON even with `Accept: application/pdf`. GET, HEAD and OPTIONS are supported.

Successful responses are PDF attachments with private/no-store cache headers.
Documents are generated in memory on demand; there are no public PDF media URLs
or report database records. Reprints use current party/product names and branding,
with saved order prices and address snapshots where the existing model has them.
Pending and finalized documents can both be downloaded.

Download actions appear on purchase/sale/transfer lists and details through the
report context processor and one shared JavaScript module. The shared table only
provides a generic row ID; all report action logic stays in this app.

## Previews and tests

```powershell
python manage.py report_samples
python -m pip install -r report/requirements-test.txt
python manage.py test report
```

Previews use synthetic data and write three documents to `output/pdf/` without
creating database records. The test dependencies provide PDF text inspection and
page rasterization. Frontend checks: `node report/tests/download_ui.mjs`.
