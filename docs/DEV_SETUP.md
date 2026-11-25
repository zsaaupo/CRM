# Developer Setup

## Python & Dependencies
Use the repository virtual environment:

```powershell
.\venv\Scripts\python.exe manage.py check
.\venv\Scripts\python.exe manage.py test
```

## PDF Rendering (WeasyPrint)
- On Windows, WeasyPrint 70.0 native DLLs are stored in `.runtime/weasyprint/onedir/weasyprint/_internal` and loaded automatically by `report/rendering.py`.
- If setting up on a fresh Windows machine, run:
  ```powershell
  .\venv\Scripts\python.exe manage.py setup_report_runtime
  ```
- Test PDF inspection libraries (`pypdf`, `PyMuPDF`) are listed in `report/requirements-test.txt`.

## Baseline Verification
- At baseline `261c0fa` on `feature/enhancement`, `manage.py check` passes with 0 issues and `manage.py test` runs 729 tests with 0 failures.
- Note: `CACHES` uses Django's default `LocMemCache` in local development; configure a shared cache backend (e.g. Redis) for multi-worker production so `GeneralSetting` cache invalidation propagates across workers.
