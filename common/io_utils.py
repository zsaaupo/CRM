"""Reusable export (CSV/XLSX/PDF/Print), bulk delete, and dry-run/atomic import pipeline."""

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO
from pathlib import Path
from typing import Callable
from uuid import NAMESPACE_URL, uuid5
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
import zipfile

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from common.audit import log_audit_event
from common.exceptions import StateConflict
from common.idempotency import IdempotentResult, run_idempotent
from common.models import AuditLog, IdempotencyRecord, ImportJob


# ---------------------------------------------------------------------------
# Minimal pure-Python XLSX writer and reader (zero external dependencies)
# ---------------------------------------------------------------------------

def _col_letter(idx: int) -> str:
    """1-indexed column number to Excel letter (1 -> A, 27 -> AA)."""
    out = []
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        out.append(chr(65 + rem))
    return ''.join(reversed(out))


def build_xlsx_bytes(headers: list[str], rows: list[list], sheet_name: str = 'Export') -> bytes:
    safe_sheet = (sheet_name or 'Export')[:31].replace('/', '-').replace('\\', '-')
    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            '[Content_Types].xml',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '</Types>',
        )
        zf.writestr(
            '_rels/.rels',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/>'
            '</Relationships>',
        )
        zf.writestr(
            'xl/workbook.xml',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{escape(safe_sheet)}" sheetId="1" r:id="rId1"/></sheets>'
            '</workbook>',
        )
        zf.writestr(
            'xl/_rels/workbook.xml.rels',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/>'
            '</Relationships>',
        )
        sheet_rows = [headers, *rows]
        xml_lines = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>',
        ]
        for r_idx, row in enumerate(sheet_rows, start=1):
            cells = []
            for c_idx, val in enumerate(row, start=1):
                ref = f'{_col_letter(c_idx)}{r_idx}'
                if isinstance(val, (int, float, Decimal)) and not isinstance(val, bool) and r_idx > 1:
                    cells.append(f'<c r="{ref}" t="n"><v>{val}</v></c>')
                else:
                    text = '' if val is None else str(val)
                    cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(text)}</t></is></c>')
            xml_lines.append(f'<row r="{r_idx}">{"".join(cells)}</row>')
        xml_lines.append('</sheetData></worksheet>')
        zf.writestr('xl/worksheets/sheet1.xml', ''.join(xml_lines))
    return buf.getvalue()


def _col_index(cell_ref: str) -> int:
    letters = ''.join(ch for ch in cell_ref if ch.isalpha()).upper()
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - 64)
    return max(idx - 1, 0)


def read_xlsx_rows(raw_bytes: bytes) -> list[list[str]]:
    with zipfile.ZipFile(BytesIO(raw_bytes), 'r') as zf:
        shared = []
        if 'xl/sharedStrings.xml' in zf.namelist():
            sst = ET.fromstring(zf.read('xl/sharedStrings.xml'))
            ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
            for si in sst.findall(f'{ns}si'):
                parts = [t.text or '' for t in si.iter(f'{ns}t')]
                shared.append(''.join(parts))
        sheet_name = next((n for n in zf.namelist() if n.startswith('xl/worksheets/sheet') and n.endswith('.xml')), None)
        if sheet_name is None:
            raise ValidationError({'file': 'The Excel workbook does not contain a readable worksheet.'})
        root = ET.fromstring(zf.read(sheet_name))
        ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
        rows = []
        for row_el in root.findall(f'.//{ns}row'):
            vals = {}
            for cell in row_el.findall(f'{ns}c'):
                idx = _col_index(cell.attrib.get('r', 'A1'))
                ctype = cell.attrib.get('t', '')
                if ctype == 'inlineStr':
                    parts = [t.text or '' for t in cell.iter(f'{ns}t')]
                    vals[idx] = ''.join(parts)
                elif ctype == 's':
                    v = cell.find(f'{ns}v')
                    si_idx = int(v.text) if v is not None and v.text else 0
                    vals[idx] = shared[si_idx] if 0 <= si_idx < len(shared) else ''
                else:
                    v = cell.find(f'{ns}v')
                    vals[idx] = v.text if v is not None and v.text is not None else ''
            if vals:
                width = max(vals) + 1
                rows.append([vals.get(i, '') for i in range(width)])
        return rows


# ---------------------------------------------------------------------------
# Queryset export + bulk delete
# ---------------------------------------------------------------------------

def _resolve_value(obj, accessor):
    if callable(accessor):
        val = accessor(obj)
    elif isinstance(obj, dict):
        val = obj.get(accessor, '')
    else:
        val = obj
        for part in str(accessor).split('.'):
            if val is None:
                break
            val = getattr(val, part, None)
    return '' if val is None else val


def export_queryset(qs, fmt: str, columns: list[tuple[str, str | Callable]], *,
                    filename: str = 'export', title: str = 'Export',
                    actor=None, request=None) -> HttpResponse:
    fmt = (fmt or 'csv').lower().strip()
    if fmt not in {'csv', 'xlsx', 'pdf', 'print'}:
        raise ValidationError({'export': 'Choose csv, xlsx, pdf, or print.'})

    headers = [header for header, _ in columns]
    rows = [[_resolve_value(obj, accessor) for _, accessor in columns] for obj in qs]

    if actor is not None:
        model = getattr(qs, 'model', None)
        model_label = model._meta.model_name if model else 'queryset'
        log_audit_event(
            actor, AuditLog.Action.EXPORT, module=model._meta.app_label if model else AuditLog.Module.SETTINGS,
            changes={'format': fmt, 'count': len(rows), 'title': title},
            request=request, object_repr=f'{model_label}:{fmt}:{len(rows)}',
        )

    if fmt == 'csv':
        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([str(v) for v in row])
        resp = HttpResponse(buf.getvalue().encode('utf-8-sig'), content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="{filename}.csv"'
        return resp

    if fmt == 'xlsx':
        payload = build_xlsx_bytes(headers, rows, sheet_name=title)
        resp = HttpResponse(
            payload,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        resp['Content-Disposition'] = f'attachment; filename="{filename}.xlsx"'
        return resp

    company = {'name': 'eManager', 'address': '', 'phone': '', 'email': '', 'logo_path': ''}
    company.update(getattr(settings, 'REPORT_COMPANY', {}))
    doc = {
        'company': company,
        'title': title,
        'headers': headers,
        'rows': [[str(v) for v in row] for row in rows],
        'generated_at': timezone.localtime().strftime('%Y-%m-%d %H:%M'),
    }
    if fmt == 'print':
        html = render_to_string('common/export_table.html', {'report': doc, 'is_print': True})
        return HttpResponse(html, content_type='text/html; charset=utf-8')

    # PDF via WeasyPrint
    from report.rendering import ReportUnavailable, asset_fetcher, pdf_engine
    try:
        CSS, HTML, FontConfiguration = pdf_engine()
        fonts = FontConfiguration()
        fetch = asset_fetcher(doc['company'])
        html = render_to_string('common/export_table.html', {'report': doc, 'is_print': False})
        pdf_bytes = HTML(string=html, base_url='report-asset:///', url_fetcher=fetch).write_pdf(font_config=fonts)
    except (ImportError, OSError, ValueError, RuntimeError):
        raise ReportUnavailable() from None
    resp = HttpResponse(pdf_bytes, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{filename}.pdf"'
    return resp


class ExportListMixin:
    """Enable `?export=csv|xlsx|pdf|print` on any scope-filtered DRF list view."""

    export_columns: list[tuple[str, str | Callable]] = []
    export_filename: str = 'export'
    export_title: str = 'Export'

    def list(self, request, *args, **kwargs):
        fmt = request.query_params.get('export')
        if fmt:
            columns = self.export_columns
            if not columns and hasattr(self, 'serializer_class'):
                columns = [(f, f) for f in getattr(self.serializer_class.Meta, 'fields', [])]
            qs = self.filter_queryset(self.get_queryset())
            ids_param = request.query_params.get('ids')
            if ids_param:
                pks = [int(x) for x in ids_param.split(',') if x.strip().isdigit()]
                if pks:
                    qs = qs.filter(pk__in=pks)
            return export_queryset(
                qs, fmt, columns,
                filename=self.export_filename, title=self.export_title,
                actor=request.user, request=request,
            )
        return super().list(request, *args, **kwargs)


def bulk_delete_queryset(actor, queryset, *, permission_codename: str,
                         soft_delete_field: str = 'is_active', soft_delete_value=False,
                         request=None) -> dict:
    if not has_role_permissions(actor, (permission_codename,)):
        raise PermissionDenied('You do not have permission to delete these records.')
    model = queryset.model
    field_names = {f.name for f in model._meta.fields}
    with transaction.atomic():
        pks = list(queryset.values_list('pk', flat=True))
        if not pks:
            return {'deleted_count': 0, 'soft_deleted': False}
        if soft_delete_field in field_names:
            count = model.objects.filter(pk__in=pks).update(**{
                soft_delete_field: soft_delete_value,
                **({'modified_by': actor.get_username()} if 'modified_by' in field_names else {}),
                **({'updated_at': timezone.now()} if 'updated_at' in field_names else {}),
            })
            soft = True
        else:
            try:
                count, _ = model.objects.filter(pk__in=pks).delete()
                soft = False
            except ProtectedError as exc:
                raise StateConflict('One or more selected records are referenced and cannot be deleted.') from exc
        log_audit_event(
            actor, AuditLog.Action.DELETE, module=model._meta.app_label,
            changes={'ids': pks, 'count': count, 'soft_deleted': soft},
            request=request, object_repr=f'{model._meta.model_name}:bulk:{count}',
        )
    return {'deleted_count': count, 'soft_deleted': soft}


# ---------------------------------------------------------------------------
# Import parser + dry-run/commit pipeline (D-57)
# ---------------------------------------------------------------------------

def parse_import_file(file_obj, filename: str = '') -> tuple[list[str], list[dict]]:
    name = (filename or getattr(file_obj, 'name', '') or '').lower()
    if isinstance(file_obj, str):
        raw_bytes = file_obj.encode('utf-8')
    elif hasattr(file_obj, 'read'):
        data = file_obj.read()
        raw_bytes = data.encode('utf-8') if isinstance(data, str) else bytes(data)
    else:
        raw_bytes = bytes(file_obj)

    if not raw_bytes.strip():
        raise ValidationError({'file': 'The uploaded import file is empty.'})

    if name.endswith('.xlsx'):
        grid = read_xlsx_rows(raw_bytes)
        if not grid:
            raise ValidationError({'file': 'No rows found in Excel file.'})
        headers = [str(h).strip() for h in grid[0]]
        raw_rows = grid[1:]
        rows = []
        for idx, vals in enumerate(raw_rows, start=2):
            if not any(str(v).strip() for v in vals):
                continue
            row = {headers[i]: (str(vals[i]).strip() if i < len(vals) else '') for i in range(len(headers)) if headers[i]}
            row['_row_number'] = idx
            rows.append(row)
        return headers, rows

    try:
        text = raw_bytes.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValidationError({'file': 'Encode the CSV file as UTF-8.'}) from exc

    reader = csv.reader(StringIO(text))
    all_rows = list(reader)
    if not all_rows:
        raise ValidationError({'file': 'No header row found in CSV file.'})
    headers = [h.strip() for h in all_rows[0]]
    rows = []
    for idx, vals in enumerate(all_rows[1:], start=2):
        if not any(str(v).strip() for v in vals):
            continue
        row = {headers[i]: (vals[i].strip() if i < len(vals) else '') for i in range(len(headers)) if headers[i]}
        row['_row_number'] = idx
        rows.append(row)
    return headers, rows


@dataclass
class ImportKindSpec:
    kind: str
    required_columns: list[str]
    optional_columns: list[str]
    sample_row: list[str]
    permissions: tuple[str, ...]
    validate_fn: Callable
    commit_group_fn: Callable
    group_by_doc_ref: bool = False


IMPORT_SPECS: dict[str, ImportKindSpec] = {}


def register_import_spec(spec: ImportKindSpec):
    IMPORT_SPECS[spec.kind] = spec


def get_import_spec(kind: str) -> ImportKindSpec:
    _ensure_builtin_import_specs()
    spec = IMPORT_SPECS.get(kind)
    if spec is None:
        raise ValidationError({'kind': f'Unsupported import kind "{kind}".'})
    return spec


def _parse_bool(raw: str, default: bool = False) -> bool:
    if raw == '':
        return default
    val = raw.strip().lower()
    if val in ('1', 'true', 'yes', 'y'):
        return True
    if val in ('0', 'false', 'no', 'n'):
        return False
    raise ValueError(f'Invalid boolean "{raw}".')


def _parse_decimal(raw: str, default: Decimal | None = None, *, min_value: Decimal | None = None) -> Decimal:
    if raw == '':
        if default is not None:
            return default
        raise ValueError('Value is required.')
    try:
        d = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f'Invalid decimal "{raw}".') from exc
    if not d.is_finite():
        raise ValueError(f'Invalid decimal "{raw}".')
    if min_value is not None and d < min_value:
        raise ValueError(f'Value must be >= {min_value}.')
    return d


def _normalize_external_key(kind: str, raw_key: str) -> tuple[str, str]:
    key = raw_key.strip()
    if not key:
        raise ValueError('external_key is required.')
    uuid_key = str(uuid5(NAMESPACE_URL, f'emanager:import:{kind}:{key}'))
    return key, uuid_key


def _ensure_builtin_import_specs():
    if IMPORT_SPECS:
        return
    from accounting.models import ExpenseCategory, IncomeCategory
    from inventory.models import Brand, Currency, Product, ProductCategory, ProductType, TaxRule, UnitOfMeasure
    from people.models import Biller, CustomerGroup, Party
    from warehouse.models import WareHouse
    from warehouse.scope import all_warehouse_access, warehouse_scope

    # 1. Warehouses
    def _val_wh(row, actor, ctx):
        if not row.get('name'):
            raise ValueError('name is required.')
        return {'name': row['name'], 'code': row.get('code', ''), 'address': row.get('address', '') or '-',
                'phone': row.get('phone', '') or '-', 'email': row.get('email', '')}

    def _commit_wh(rows, actor, ctx):
        created = []
        for r in rows:
            obj, _ = WareHouse.objects.get_or_create(
                name=r['name'],
                defaults={'code': r['code'], 'address': r['address'], 'phone': r['phone'],
                          'email': r['email'] or None, 'create_by': actor.get_username(),
                          'modified_by': actor.get_username()},
            )
            created.append(obj.pk)
        return created

    register_import_spec(ImportKindSpec(
        'warehouses', ['external_key', 'name'], ['code', 'address', 'phone', 'email'],
        ['wh-main', 'Main Warehouse', 'MAIN', 'Dhaka', '01700000000', 'wh@example.com'],
        ('warehouse.add_warehouse',), _val_wh, _commit_wh,
    ))

    # 2. Categories
    def _val_cat(row, actor, ctx):
        if not row.get('name') or not row.get('code'):
            raise ValueError('name and code are required.')
        return {'name': row['name'], 'code': row['code'], 'description': row.get('description', '')}

    def _commit_cat(rows, actor, ctx):
        created = []
        for r in rows:
            obj, _ = ProductCategory.objects.get_or_create(
                code=r['code'],
                defaults={'name': r['name'], 'description': r['description'],
                          'create_by': actor.get_username(), 'modified_by': actor.get_username()},
            )
            created.append(obj.pk)
        return created

    register_import_spec(ImportKindSpec(
        'categories', ['external_key', 'name', 'code'], ['description'],
        ['cat-1', 'Electronics', 'ELEC', 'Electronic devices'],
        ('inventory.add_productcategory',), _val_cat, _commit_cat,
    ))

    # 3. Brands
    def _val_brand(row, actor, ctx):
        if not row.get('name'):
            raise ValueError('name is required.')
        return {'name': row['name'], 'website': row.get('website', ''), 'description': row.get('description', '')}

    def _commit_brand(rows, actor, ctx):
        created = []
        for r in rows:
            obj, _ = Brand.objects.get_or_create(
                name=r['name'],
                defaults={'website': r['website'], 'description': r['description'],
                          'create_by': actor.get_username(), 'modified_by': actor.get_username()},
            )
            created.append(obj.pk)
        return created

    register_import_spec(ImportKindSpec(
        'brands', ['external_key', 'name'], ['website', 'description'],
        ['brand-1', 'Acme', 'https://acme.example.com', 'Standard brand'],
        ('inventory.add_brand',), _val_brand, _commit_brand,
    ))

    # 4. Units
    def _val_unit(row, actor, ctx):
        if not row.get('name') or not row.get('code'):
            raise ValueError('name and code are required.')
        return {'name': row['name'], 'code': row['code']}

    def _commit_unit(rows, actor, ctx):
        created = []
        for r in rows:
            obj, _ = UnitOfMeasure.objects.get_or_create(
                code=r['code'],
                defaults={'name': r['name'], 'create_by': actor.get_username(), 'modified_by': actor.get_username()},
            )
            created.append(obj.pk)
        return created

    register_import_spec(ImportKindSpec(
        'units', ['external_key', 'name', 'code'], [],
        ['unit-1', 'Pieces', 'PCS'],
        ('inventory.add_unitofmeasure',), _val_unit, _commit_unit,
    ))

    # 4b. Tax rules
    def _val_tax(row, actor, ctx):
        if not row.get('name'):
            raise ValueError('name is required.')
        rate = _parse_decimal(row.get('rate', ''), min_value=Decimal('0'))
        if rate > Decimal('100'):
            raise ValueError('rate cannot exceed 100.')
        return {'name': row['name'], 'rate': str(rate)}

    def _commit_tax(rows, actor, ctx):
        created = []
        for r in rows:
            obj, _ = TaxRule.objects.get_or_create(
                name=r['name'],
                defaults={'rate': Decimal(r['rate']), 'create_by': actor.get_username(), 'modified_by': actor.get_username()},
            )
            created.append(obj.pk)
        return created

    register_import_spec(ImportKindSpec(
        'tax_rules', ['external_key', 'name', 'rate'], [],
        ['tax-1', 'VAT 15', '15'],
        ('inventory.add_taxrule',), _val_tax, _commit_tax,
    ))

    # 5. Customer groups
    def _val_cg(row, actor, ctx):
        if not row.get('name'):
            raise ValueError('name is required.')
        pct = _parse_decimal(row.get('discount_percentage', '0'), Decimal('0'), min_value=Decimal('0'))
        if pct > Decimal('100'):
            raise ValueError('discount_percentage cannot exceed 100.')
        return {'name': row['name'], 'discount_percentage': str(pct)}

    def _commit_cg(rows, actor, ctx):
        created = []
        for r in rows:
            obj, _ = CustomerGroup.objects.get_or_create(
                name=r['name'],
                defaults={'percentage': Decimal(r['discount_percentage']),
                          'create_by': actor.get_username(), 'modified_by': actor.get_username()},
            )
            created.append(obj.pk)
        return created

    register_import_spec(ImportKindSpec(
        'customer_groups', ['external_key', 'name'], ['discount_percentage'],
        ['cg-vip', 'VIP', '5'],
        ('people.add_customergroup',), _val_cg, _commit_cg,
    ))

    # 6. Expense / Income categories
    def _val_fin_cat(row, actor, ctx):
        if not row.get('name'):
            raise ValueError('name is required.')
        return {'name': row['name'], 'description': row.get('description', '')}

    def _commit_exp_cat(rows, actor, ctx):
        created = []
        for r in rows:
            obj, _ = ExpenseCategory.objects.get_or_create(
                name=r['name'],
                defaults={'description': r['description'], 'create_by': actor.get_username(), 'modified_by': actor.get_username()},
            )
            created.append(obj.pk)
        return created

    def _commit_inc_cat(rows, actor, ctx):
        created = []
        for r in rows:
            obj, _ = IncomeCategory.objects.get_or_create(
                name=r['name'],
                defaults={'description': r['description'], 'create_by': actor.get_username(), 'modified_by': actor.get_username()},
            )
            created.append(obj.pk)
        return created

    register_import_spec(ImportKindSpec(
        'expense_categories', ['external_key', 'name'], ['description'],
        ['expcat-1', 'Utilities', 'Power and water'],
        ('accounting.add_expensecategory',), _val_fin_cat, _commit_exp_cat,
    ))
    register_import_spec(ImportKindSpec(
        'income_categories', ['external_key', 'name'], ['description'],
        ['inccat-1', 'Services', 'Consulting revenue'],
        ('accounting.add_incomecategory',), _val_fin_cat, _commit_inc_cat,
    ))

    # 7. Products
    def _val_prod(row, actor, ctx):
        for col in ('sku', 'name', 'unit_code'):
            if not row.get(col):
                raise ValueError(f'{col} is required.')
        unit = UnitOfMeasure.objects.filter(code=row['unit_code']).first()
        if unit is None:
            raise ValueError(f'Unknown unit_code "{row["unit_code"]}".')
        cat = None
        if row.get('category_code'):
            cat = ProductCategory.objects.filter(code=row['category_code']).first()
            if cat is None:
                raise ValueError(f'Unknown category_code "{row["category_code"]}".')
        brand = None
        if row.get('brand_name'):
            brand = Brand.objects.filter(name=row['brand_name']).first()
            if brand is None:
                raise ValueError(f'Unknown brand_name "{row["brand_name"]}".')
        ptype_code = row.get('product_type_code') or 'goods'
        if not ProductType.objects.filter(code=ptype_code).exists():
            raise ValueError(f'Unknown product_type_code "{ptype_code}".')
        curr_code = row.get('currency_code') or settings.INVENTORY_BASE_CURRENCY
        if not Currency.objects.filter(code=curr_code).exists():
            raise ValueError(f'Unknown currency_code "{curr_code}".')
        cost = _parse_decimal(row.get('cost_price', '0'), Decimal('0'), min_value=Decimal('0'))
        price = _parse_decimal(row.get('sale_price', '0'), Decimal('0'), min_value=Decimal('0'))
        return {
            'sku': row['sku'], 'name': row['name'], 'unit_id': unit.pk,
            'category_id': cat.pk if cat else None, 'brand_id': brand.pk if brand else None,
            'product_type_id': ptype_code, 'currency_id': curr_code,
            'cost_price': str(cost), 'sale_price': str(price),
        }

    def _commit_prod(rows, actor, ctx):
        created = []
        for r in rows:
            prod, _ = Product.objects.get_or_create(
                sku=r['sku'],
                defaults={
                    'name': r['name'], 'unit_id': r['unit_id'], 'category_id': r['category_id'],
                    'brand_id': r['brand_id'], 'product_type_id': r['product_type_id'],
                    'currency_id': r['currency_id'], 'cost_price': Decimal(r['cost_price']),
                    'sale_price': Decimal(r['sale_price']),
                    'create_by': actor.get_username(), 'modified_by': actor.get_username(),
                },
            )
            created.append(prod.pk)
        return created

    register_import_spec(ImportKindSpec(
        'products', ['external_key', 'sku', 'name', 'unit_code'],
        ['category_code', 'brand_name', 'cost_price', 'sale_price', 'product_type_code', 'currency_code'],
        ['prod-1', 'SKU-001', 'Sample Widget', 'PCS', 'ELEC', 'Acme', '50', '80', 'goods', 'BDT'],
        ('inventory.add_product',), _val_prod, _commit_prod,
    ))

    # 8. Parties
    def _val_party(row, actor, ctx):
        if not row.get('name') or not row.get('code'):
            raise ValueError('name and code are required.')
        is_cust = _parse_bool(row.get('is_customer', '1'), True)
        is_supp = _parse_bool(row.get('is_supplier', '0'), False)
        if not (is_cust or is_supp):
            raise ValueError('At least one of is_customer or is_supplier must be true.')
        return {
            'name': row['name'], 'code': row['code'], 'is_customer': is_cust, 'is_supplier': is_supp,
            'phone': row.get('phone', ''), 'email': row.get('email', ''), 'address': row.get('address', ''),
        }

    def _commit_party(rows, actor, ctx):
        created = []
        for r in rows:
            obj, _ = Party.objects.get_or_create(
                code=r['code'],
                defaults={
                    'name': r['name'], 'is_customer': r['is_customer'], 'is_supplier': r['is_supplier'],
                    'phone': r['phone'], 'email': r['email'], 'address': r['address'],
                    'create_by': actor.get_username(), 'modified_by': actor.get_username(),
                },
            )
            created.append(obj.pk)
        return created

    register_import_spec(ImportKindSpec(
        'parties', ['external_key', 'name', 'code'], ['is_customer', 'is_supplier', 'phone', 'email', 'address'],
        ['pty-1', 'Walk-in Customer', 'CUST-001', 'true', 'false', '01700000000', 'c@example.com', 'Dhaka'],
        ('people.add_party',), _val_party, _commit_party,
    ))

    # 9. Billers
    def _val_biller(row, actor, ctx):
        if not row.get('name'):
            raise ValueError('name is required.')
        wh_id = row.get('warehouse_id') or row.get('warehouse')
        scope = warehouse_scope(actor)
        resolved_wh = None
        if wh_id:
            if not str(wh_id).isdigit():
                wh = WareHouse.objects.filter(name=wh_id, is_active=True).first()
            else:
                wh = WareHouse.objects.filter(pk=int(wh_id), is_active=True).first()
            if wh is None:
                raise ValueError(f'Unknown warehouse "{wh_id}".')
            if not all_warehouse_access(scope) and wh.pk != scope.warehouse_id:
                raise ValueError(f'Warehouse "{wh_id}" is outside your access scope.')
            resolved_wh = wh.pk
        return {
            'name': row['name'], 'company_name': row.get('company_name', ''),
            'email': row.get('email', ''), 'phone': row.get('phone', ''),
            'address': row.get('address', ''), 'vat_number': row.get('vat_number', ''),
            'warehouse_id': resolved_wh,
        }

    def _commit_biller(rows, actor, ctx):
        from people.models import BillerWarehouse
        created = []
        for r in rows:
            b, _ = Biller.objects.get_or_create(
                name=r['name'],
                defaults={
                    'company_name': r['company_name'], 'email': r['email'], 'phone': r['phone'],
                    'address': r['address'], 'vat_number': r['vat_number'],
                    'create_by': actor.get_username(), 'modified_by': actor.get_username(),
                },
            )
            if r['warehouse_id']:
                BillerWarehouse.objects.get_or_create(biller=b, warehouse_id=r['warehouse_id'])
            created.append(b.pk)
        return created

    register_import_spec(ImportKindSpec(
        'billers', ['external_key', 'name'], ['company_name', 'email', 'phone', 'address', 'vat_number', 'warehouse'],
        ['bil-1', 'Dhaka Desk', 'eManager Ltd', 'desk@example.com', '01700000001', 'Dhaka', 'VAT-1', ''],
        ('people.manage_biller',), _val_biller, _commit_biller,
    ))


def generate_import_template(kind: str) -> HttpResponse:
    spec = get_import_spec(kind)
    headers = list(dict.fromkeys([*spec.required_columns, *spec.optional_columns]))
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    if spec.sample_row:
        writer.writerow(spec.sample_row[:len(headers)])
    resp = HttpResponse(buf.getvalue().encode('utf-8-sig'), content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="{kind}_template.csv"'
    return resp


def create_and_validate_import_job(actor, kind: str, file_obj, *, filename: str = 'import.csv',
                                   context: dict | None = None, request=None) -> ImportJob:
    spec = get_import_spec(kind)
    if not has_role_permissions(actor, spec.permissions):
        raise PermissionDenied(f'You do not have permission to import {kind}.')

    ctx = dict(context or {})
    if isinstance(file_obj, str):
        raw_bytes = file_obj.encode('utf-8')
        buf = BytesIO(raw_bytes)
    else:
        raw_bytes = file_obj.read()
        if isinstance(raw_bytes, str):
            raw_bytes = raw_bytes.encode('utf-8')
        buf = BytesIO(raw_bytes)

    headers, rows = parse_import_file(BytesIO(raw_bytes), filename=filename)
    missing_cols = [c for c in spec.required_columns if c not in headers]
    errors = []
    if missing_cols:
        errors.append({'row': 1, 'field': 'headers', 'error': f'Missing required columns: {", ".join(missing_cols)}'})

    seen_keys = set()
    duplicates = []
    parsed_rows = []
    row_scope = f'import_row:{kind}'

    if not missing_cols:
        for row in rows:
            rnum = row['_row_number']
            raw_ext = row.get('external_key', '')
            try:
                ext_key, uuid_key = _normalize_external_key(kind, raw_ext)
            except ValueError as exc:
                errors.append({'row': rnum, 'field': 'external_key', 'error': str(exc)})
                continue
            if ext_key in seen_keys:
                errors.append({'row': rnum, 'field': 'external_key', 'error': f'Duplicate external_key "{ext_key}" in file.'})
                continue
            seen_keys.add(ext_key)

            already = IdempotencyRecord.objects.filter(key=uuid_key, scope=row_scope).exists()
            try:
                normalized = spec.validate_fn(row, actor, ctx)
            except (ValueError, ValidationError) as exc:
                msg = getattr(exc, 'detail', str(exc))
                errors.append({'row': rnum, 'field': 'row', 'error': str(msg)})
                continue

            doc_ref = (row.get('external_document_ref') or '').strip() if spec.group_by_doc_ref else ext_key
            item = {
                'row_number': rnum,
                'external_key': ext_key,
                'uuid_key': uuid_key,
                'doc_ref': doc_ref or ext_key,
                'already_imported': already,
                'data': normalized,
            }
            if already:
                duplicates.append({'row': rnum, 'external_key': ext_key})
            parsed_rows.append(item)

    if not rows and not errors:
        errors.append({'row': 0, 'field': 'file', 'error': 'No data rows found in import file.'})

    valid = len(errors) == 0
    dry_run_result = {
        'valid': valid,
        'kind': kind,
        'total_rows': len(rows),
        'new_rows': sum(1 for r in parsed_rows if not r['already_imported']),
        'duplicates': duplicates,
        'errors': errors,
        'parsed_rows': parsed_rows if valid else [],
    }
    job = ImportJob.objects.create(
        kind=kind,
        status=ImportJob.Status.VALIDATED if valid else ImportJob.Status.FAILED,
        dry_run_result=dry_run_result,
        created_by=actor if getattr(actor, 'is_authenticated', False) else None,
        create_by=actor.get_username() if hasattr(actor, 'get_username') else '',
        modified_by=actor.get_username() if hasattr(actor, 'get_username') else '',
    )
    job.file.save(Path(filename).name or f'{kind}.csv', ContentFile(raw_bytes), save=True)
    return job


def commit_import_job(actor, job_id: int, request_key, *, context: dict | None = None,
                      request=None) -> tuple[ImportJob, bool]:
    job_preview = ImportJob.objects.filter(pk=job_id).first()
    if job_preview is None:
        from rest_framework.exceptions import NotFound
        raise NotFound('Import job not found.')

    spec = get_import_spec(job_preview.kind)
    if not has_role_permissions(actor, spec.permissions):
        raise PermissionDenied(f'You do not have permission to import {job_preview.kind}.')

    scope = f'import_job:{job_preview.kind}:{job_preview.pk}'
    payload = {'job_id': job_preview.pk, 'kind': job_preview.kind}

    def _loader(rec):
        return ImportJob.objects.get(pk=rec.result_id)

    def _do():
        with transaction.atomic():
            job = ImportJob.objects.select_for_update().get(pk=job_id)
            if not has_role_permissions(actor, spec.permissions):
                raise PermissionDenied(f'You do not have permission to import {spec.kind}.')
            dry = job.dry_run_result or {}
            if job.status == ImportJob.Status.FAILED or not dry.get('valid'):
                raise ValidationError({'import': dry.get('errors') or ['Fix validation errors before committing.']})
            parsed = dry.get('parsed_rows', [])
            ctx = dict(context or {})
            row_scope = f'import_row:{spec.kind}'

            # Group rows by external_document_ref (for multi-line documents) or single row
            groups: dict[str, list[dict]] = {}
            for entry in parsed:
                groups.setdefault(entry['doc_ref'], []).append(entry)

            committed_ids = []
            skipped_keys = []
            for doc_ref, entries in groups.items():
                # Check if ALL rows in this group were already imported by stable external_key
                pending = []
                for entry in entries:
                    if IdempotencyRecord.objects.filter(key=entry['uuid_key'], scope=row_scope).exists():
                        skipped_keys.append(entry['external_key'])
                    else:
                        pending.append(entry)
                if not pending:
                    continue
                with transaction.atomic():
                    # Double check inside atomic block & re-validate scope/rows before writing
                    ids = spec.commit_group_fn([e['data'] for e in pending], actor, ctx)
                    committed_ids.extend(ids)
                    for idx, entry in enumerate(pending):
                        rec_id = ids[idx] if idx < len(ids) else (ids[0] if ids else 0)
                        try:
                            IdempotencyRecord.objects.create(
                                key=entry['uuid_key'],
                                scope=row_scope,
                                payload_hash=entry['external_key'],
                                result_type=spec.kind,
                                result_id=rec_id,
                                result_data={'doc_ref': doc_ref},
                            )
                        except IntegrityError:
                            raise StateConflict(f'Duplicate external_key "{entry["external_key"]}" committed concurrently.')

            job.status = ImportJob.Status.COMMITTED
            job.request_key = request_key
            job.committed_result = {
                'committed_count': len(committed_ids),
                'committed_ids': committed_ids,
                'skipped_duplicates': skipped_keys,
            }
            job.save(update_fields=['status', 'request_key', 'committed_result', 'updated_at'])
            log_audit_event(
                actor, AuditLog.Action.IMPORT, job,
                changes={'kind': spec.kind, 'committed': len(committed_ids), 'skipped': len(skipped_keys)},
                request=request, request_key=request_key,
            )
            return IdempotentResult(
                result_type='import_job', result_id=job.pk,
                result_data=job.committed_result, value=job,
            )

    return run_idempotent(request_key, scope, payload, _do, loader=_loader)
