# Deviations from Specification

| WP | Doc Name | Repo Name / State | Reason |
|---|---|---|---|
| Pre-M0 | Branch `feature/automatic-payments` | Branch `feature/enhancement` (commit `261c0fa`) | Current active development branch is `feature/enhancement` at `261c0fa`. |
| Pre-M0 | §7.1 Baseline: 718 tests, 2 failures, 1 import error | 729 tests, 0 failures, 0 errors | `.runtime/weasyprint` and `pypdf==7.1.0` are already installed in `venv`. |
| FND-01 | `created_by` (audit string on `TimeStampedModel`) | `create_by` / `modified_by` (`common.models.TimeStampedModel`) | Existing base model uses `create_by` and `modified_by`; new models inheriting `TimeStampedModel` keep `create_by`/`modified_by`, and user FKs use `created_by`. |
| INV-02 | `Product.wholesale_price` | `Product.Whole_sale_price` (`inventory/models.py`) | Existing `Product` model defines `Whole_sale_price`. |
| FIN-01 | `accounting/test_finance.py` line 94 asserted 405 on `POST sales-payment-list` | Updated assertion to 400 on invalid/unposted POST | FIN-01 DoD makes `/accounting/api/sales-orders/{id}/payments/` writable (`POST` creates payments, rejects empty/unposted with 400). |
| OPS-08 / INV-02 | `openpyxl` & `python-barcode` libraries | Pure-Python zero-dependency ZIP/XLSX (`common/io_utils.py`) & SVG barcode renderer (`inventory/barcodes.py`) | Neither external package was installed in `venv`; pure-Python implementations produce standard OOXML `.xlsx` and SVG/PDF barcodes with no extra dependencies. |
| MFG-01 | `BOMComponent.issue_multiple` default 1 | `default=None` (`null=True, blank=True`) | Keeps unconfigured components exact (`8.8` / `8.888889` per F-MFG-1) and rounds up when an explicit multiple is configured (`0.5` -> `9.0`). |
