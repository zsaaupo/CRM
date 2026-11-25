"""Pure-Python SVG barcode encoder + WeasyPrint sheet/thermal PDF generator."""

from decimal import Decimal

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from common.settings_store import get_general
from inventory.models import Product
from report.rendering import ReportUnavailable, asset_fetcher, pdf_engine


# Code128 patterns (values 0..106) -> 6 bar/space widths; Stop (106) has 7 widths.
_CODE128_PATTERNS = [
    '212222', '222122', '222221', '121223', '121322', '131222', '122213', '122312', '132212', '221213',
    '221312', '231212', '112232', '122132', '122231', '113222', '123122', '123221', '223211', '221132',
    '221231', '213212', '223112', '312131', '311222', '321122', '321221', '312212', '322112', '322211',
    '212123', '212321', '232121', '111323', '131123', '131321', '112313', '132113', '132311', '211313',
    '231113', '231311', '112133', '112331', '132131', '113123', '113321', '133121', '313121', '211331',
    '231131', '213113', '213311', '213131', '311123', '311321', '331121', '312113', '312311', '332111',
    '314111', '221411', '431111', '111224', '111422', '121124', '121421', '141122', '141221', '112214',
    '112412', '122114', '122411', '142112', '142211', '241211', '221114', '413111', '241112', '134111',
    '111242', '121142', '121241', '114212', '124112', '124211', '411212', '421112', '421211', '212141',
    '214121', '412121', '111143', '111341', '131141', '114113', '114311', '411113', '411311', '113141',
    '114131', '311141', '411131', '211412', '211214', '211232', '2331112',
]

# Code39 encodings ('1'=wide, '0'=narrow, alternating bar/space)
_CODE39_TABLE = {
    '0': '000110100', '1': '100100001', '2': '001100001', '3': '101100000',
    '4': '000110001', '5': '100110000', '6': '001110000', '7': '000100101',
    '8': '100100100', '9': '001100100', 'A': '100001001', 'B': '001001001',
    'C': '101001000', 'D': '000011001', 'E': '100011000', 'F': '001011000',
    'G': '000001101', 'H': '100001100', 'I': '001001100', 'J': '000011100',
    'K': '100000011', 'L': '001000011', 'M': '101000010', 'N': '000010011',
    'O': '100010010', 'P': '001010010', 'Q': '000000111', 'R': '100000110',
    'S': '001000110', 'T': '000010110', 'U': '110000001', 'V': '011000001',
    'W': '111000000', 'X': '010010001', 'Y': '110010000', 'Z': '011010000',
    '-': '010000101', '.': '110000100', ' ': '011000100', '$': '010101000',
    '/': '010100010', '+': '010001010', '%': '000101010', '*': '010010100',
}

_EAN_L = ['0001101', '0011001', '0010011', '0111101', '0100011', '0110001', '0101111', '0111011', '0110111', '0001011']
_EAN_G = ['0100111', '0110011', '0011011', '0100001', '0011101', '0111001', '0000101', '0010001', '0001001', '0010111']
_EAN_R = ['1110010', '1100110', '1101100', '1000010', '1011100', '1001110', '1010000', '1000100', '1001000', '1110100']
_EAN13_PARITY = ['LLLLLL', 'LLGLGG', 'LLGGLG', 'LLGGGL', 'LGLLGG', 'LGGLLG', 'LGGGLL', 'LGLGLG', 'LGLGGL', 'LGGLGL']


def _ean_check_digit(digits_12: str) -> str:
    total = sum(int(d) * (1 if idx % 2 == 0 else 3) for idx, d in enumerate(digits_12))
    return str((10 - (total % 10)) % 10)


def _upca_check_digit(digits_11: str) -> str:
    total = sum(int(d) * (3 if idx % 2 == 0 else 1) for idx, d in enumerate(digits_11))
    return str((10 - (total % 10)) % 10)


def validate_and_render_svg(code: str, symbology: str) -> tuple[str, str]:
    """Validate value for symbology and return (normalized_code, inline_svg)."""
    value = (code or '').strip()
    sym = (symbology or 'code128').lower()
    if not value:
        raise ValueError('Barcode value is empty.')

    bars: list[tuple[int, int]] = []  # list of (x_units, width_units)
    x = 2  # quiet zone left

    if sym == 'code128':
        codes = [104]  # Start Code B
        for ch in value:
            o = ord(ch)
            if not (32 <= o <= 126):
                raise ValueError(f'Unsupported character {ch!r} for Code 128.')
            codes.append(o - 32)
        checksum = (codes[0] + sum(idx * val for idx, val in enumerate(codes[1:], start=1))) % 103
        codes.extend([checksum, 106])
        for pat_idx in codes:
            pat = _CODE128_PATTERNS[pat_idx]
            for pos, wch in enumerate(pat):
                w = int(wch)
                if pos % 2 == 0:
                    bars.append((x, w))
                x += w

    elif sym == 'code39':
        upper = value.upper()
        for ch in upper:
            if ch not in _CODE39_TABLE or ch == '*':
                raise ValueError(f'Unsupported character {ch!r} for Code 39.')
        encoded = f'*{upper}*'
        for idx, ch in enumerate(encoded):
            pat = _CODE39_TABLE[ch]
            for pos, wch in enumerate(pat):
                w = 3 if wch == '1' else 1
                if pos % 2 == 0:
                    bars.append((x, w))
                x += w
            if idx < len(encoded) - 1:
                x += 1
        value = upper

    elif sym == 'ean13':
        if not value.isdigit() or len(value) not in (12, 13):
            raise ValueError('EAN-13 requires 12 or 13 numeric digits.')
        expected_chk = _ean_check_digit(value[:12])
        if len(value) == 13 and value[12] != expected_chk:
            raise ValueError(f'Invalid EAN-13 check digit (expected {expected_chk}, got {value[12]}).')
        if len(value) == 12:
            value = value + expected_chk
        bits = ['101']
        parity = _EAN13_PARITY[int(value[0])]
        for i, dch in enumerate(value[1:7]):
            bits.append(_EAN_L[int(dch)] if parity[i] == 'L' else _EAN_G[int(dch)])
        bits.append('01010')
        for dch in value[7:13]:
            bits.append(_EAN_R[int(dch)])
        bits.append('101')
        bitstr = ''.join(bits)
        for bit in bitstr:
            if bit == '1':
                bars.append((x, 1))
            x += 1

    elif sym == 'upca':
        if not value.isdigit() or len(value) not in (11, 12):
            raise ValueError('UPC-A requires 11 or 12 numeric digits.')
        expected_chk = _upca_check_digit(value[:11])
        if len(value) == 12 and value[11] != expected_chk:
            raise ValueError(f'Invalid UPC-A check digit (expected {expected_chk}, got {value[11]}).')
        if len(value) == 11:
            value = value + expected_chk
        bits = ['101'] + [_EAN_L[int(d)] for d in value[:6]] + ['01010'] + [_EAN_R[int(d)] for d in value[6:]] + ['101']
        for bit in ''.join(bits):
            if bit == '1':
                bars.append((x, 1))
            x += 1
    else:
        raise ValueError(f'Unsupported symbology "{symbology}".')

    total_w = x + 2
    rects = ''.join(f'<rect x="{bx}" y="2" width="{bw}" height="38" fill="#000"/>' for bx, bw in bars)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total_w} 42" '
        f'width="100%" height="34" preserveAspectRatio="none" '
        f'data-symbology="{sym}" data-code="{value}">{rects}</svg>'
    )
    return value, svg


LAYOUT_PRESETS = {
    'a4_20': {'cols': 4, 'rows': 5, 'per_page': 20, 'page_size': 'A4 portrait'},
    '20': {'cols': 4, 'rows': 5, 'per_page': 20, 'page_size': 'A4 portrait'},
    'a4_24': {'cols': 3, 'rows': 8, 'per_page': 24, 'page_size': 'A4 portrait'},
    '24': {'cols': 3, 'rows': 8, 'per_page': 24, 'page_size': 'A4 portrait'},
    'a4_30': {'cols': 3, 'rows': 10, 'per_page': 30, 'page_size': 'A4 portrait'},
    '30': {'cols': 3, 'rows': 10, 'per_page': 30, 'page_size': 'A4 portrait'},
    'a4_40': {'cols': 4, 'rows': 10, 'per_page': 40, 'page_size': 'A4 portrait'},
    '40': {'cols': 4, 'rows': 10, 'per_page': 40, 'page_size': 'A4 portrait'},
    'thermal': {'cols': 1, 'rows': 1, 'per_page': 1, 'page_size': '50mm 30mm'},
}


def generate_barcodes_pdf(items: list[dict], *, layout: str = 'a4_24', fields: dict | None = None) -> bytes:
    if not items:
        raise ValidationError({'items': 'Add at least one product label to print.'})
    layout_key = str(layout or 'a4_24').lower()
    preset = LAYOUT_PRESETS.get(layout_key)
    if preset is None:
        raise ValidationError({'layout': f'Unsupported label layout "{layout}". Choose a4_20, a4_24, a4_30, a4_40, or thermal.'})

    cfg = {
        'product_name': {'show': True, 'size': 9},
        'price': {'show': True, 'size': 9},
        'promo_price': {'show': False, 'size': 8},
        'business_name': {'show': True, 'size': 8},
    }
    if fields:
        for k, v in fields.items():
            if k in cfg and isinstance(v, dict):
                cfg[k] = {'show': bool(v.get('show', cfg[k]['show'])), 'size': int(v.get('size', cfg[k]['size']))}

    business_name = get_general().company_name or getattr(settings, 'REPORT_COMPANY', {}).get('name', 'eManager')
    labels = []
    errors = []
    prod_ids = [it.get('product') for it in items]
    products = Product.objects.in_bulk(prod_ids)

    for idx, entry in enumerate(items, start=1):
        prod = products.get(entry.get('product'))
        if prod is None:
            errors.append({'row': idx, 'field': 'product', 'error': f'Product #{entry.get("product")} not found.'})
            continue
        copies = int(entry.get('copies') or 1)
        if copies < 1 or copies > 500:
            errors.append({'row': idx, 'field': 'copies', 'error': 'Copies must be between 1 and 500.'})
            continue
        raw_code = (prod.barcode or prod.sku or '').strip()
        try:
            norm_code, svg = validate_and_render_svg(raw_code, prod.barcode_symbology)
        except ValueError as exc:
            errors.append({'row': idx, 'field': 'barcode', 'sku': prod.sku, 'error': str(exc)})
            continue
        label_ctx = {
            'sku': prod.sku,
            'name': prod.name,
            'code': norm_code,
            'symbology': prod.barcode_symbology,
            'svg': svg,
            'price': f'{prod.currency_id} {Decimal(prod.sale_price):.2f}',
            'promo_price': f'{prod.currency_id} {Decimal(prod.promo_price):.2f}' if prod.is_promo and prod.promo_price is not None else '',
            'business_name': business_name,
        }
        labels.extend([label_ctx] * copies)

    if errors:
        raise ValidationError({'items': errors})

    try:
        CSS, HTML, FontConfiguration = pdf_engine()
        fonts = FontConfiguration()
        company = {'name': business_name, 'address': '', 'phone': '', 'email': '', 'logo_path': ''}
        company.update(getattr(settings, 'REPORT_COMPANY', {}))
        fetch = asset_fetcher(company)
        html = render_to_string(
            'report/barcodes.html',
            {'labels': labels, 'preset': preset, 'fields': cfg, 'generated_at': timezone.localtime().strftime('%Y-%m-%d %H:%M')},
        )
        return HTML(string=html, base_url='report-asset:///', url_fetcher=fetch).write_pdf(font_config=fonts)
    except (ImportError, OSError, ValueError, RuntimeError):
        raise ReportUnavailable() from None
