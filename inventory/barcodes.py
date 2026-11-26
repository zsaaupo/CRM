"""Calculate barcode label positions and the vector geometry sent to the PDF template."""
import re
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from itertools import groupby

from barcode import get
from django.conf import settings
from django.utils import timezone
from fontTools.ttLib import TTFont
from PIL import ImageFont

from report.rendering import ASSETS


MM_PER_INCH = Decimal('25.4')
MM_PER_PX = MM_PER_INCH / 96
LABEL_PADDING = Decimal('1')
LINE_HEIGHT = Decimal('1.15')
CAPTION_SIZE = 9
CODE_CAPTION_HEIGHT = CAPTION_SIZE * LINE_HEIGHT * MM_PER_PX
QUIET_ZONE = Decimal('2.5')
PRICE_GAP = Decimal('0.75')
MAX_LABELS = 500

LABEL_FIELDS = (
    ('product_name', 'Product name'),
    ('price', 'Price'),
    ('promo_price', 'Promotional price'),
    ('business_name', 'Business name'),
)

CUSTOM_DIMENSIONS = (
    ('sheet_width', 'Sheet width', '215.90'),
    ('sheet_height', 'Sheet height', '279.40'),
    ('label_width', 'Label width', '66.68'),
    ('label_height', 'Label height', '25.40'),
    ('margin_top', 'Top margin', '12.70'),
    ('margin_bottom', 'Bottom margin', '12.70'),
    ('margin_left', 'Left margin', '4.76'),
    ('margin_right', 'Right margin', '4.76'),
    ('gap_x', 'Column gap', '3.17'),
    ('gap_y', 'Row gap', '0'),
)


@dataclass(frozen=True)
class PaperLayout:
    sheet_width: Decimal
    sheet_height: Decimal
    label_width: Decimal
    label_height: Decimal
    margin_top: Decimal
    margin_left: Decimal
    gap_x: Decimal
    gap_y: Decimal
    columns: int
    rows: int

    @property
    def capacity(self):
        return self.columns * self.rows

    def position(self, slot):
        row, column = divmod(slot, self.columns)
        return {
            'left': self.margin_left + column * (self.label_width + self.gap_x),
            'top': self.margin_top + row * (self.label_height + self.gap_y),
        }


def letter_sheet(width, height, columns, rows, column_gap='0'):
    sheet_width, sheet_height = Decimal('8.5') * MM_PER_INCH, 11 * MM_PER_INCH
    label_width, label_height = Decimal(width) * MM_PER_INCH, Decimal(height) * MM_PER_INCH
    gap_x = Decimal(column_gap) * MM_PER_INCH
    return PaperLayout(
        sheet_width, sheet_height, label_width, label_height,
        (sheet_height - rows * label_height) / 2,
        (sheet_width - columns * label_width - (columns - 1) * gap_x) / 2,
        gap_x, Decimal(0), columns, rows,
    )


PRESETS = {
    '20_per_sheet': ('20 labels per sheet', letter_sheet('4', '1', 2, 10)),
    '30_per_sheet': ('30 labels per sheet · Avery 5160', letter_sheet('2.625', '1', 3, 10, '0.125')),
    '32_per_sheet': ('32 labels per sheet', letter_sheet('2', '1.25', 4, 8)),
    '40_per_sheet': ('40 labels per sheet', letter_sheet('2', '1', 4, 10)),
    '50_per_sheet': ('50 labels per sheet', letter_sheet('1.5', '1', 5, 10)),
    'continuous_31_25': (
        'Thermal roll · 31.75 × 25.4 mm',
        PaperLayout(Decimal('31.75'), Decimal('25.4'), Decimal('31.75'), Decimal('25.4'),
                    Decimal(0), Decimal(0), Decimal(0), Decimal(0), 1, 1),
    ),
}


def custom_layout(dimensions):
    width = dimensions['sheet_width'] - dimensions['margin_left'] - dimensions['margin_right']
    height = dimensions['sheet_height'] - dimensions['margin_top'] - dimensions['margin_bottom']
    gap_x, gap_y = dimensions['gap_x'], dimensions['gap_y']
    label_width, label_height = dimensions['label_width'], dimensions['label_height']
    if width < label_width or height < label_height:
        raise ValueError('At least one label must fit inside the sheet margins.')
    return PaperLayout(
        dimensions['sheet_width'], dimensions['sheet_height'], label_width, label_height,
        dimensions['margin_top'], dimensions['margin_left'], gap_x, gap_y,
        int((width + gap_x) // (label_width + gap_x)),
        int((height + gap_y) // (label_height + gap_y)),
    )


def money(value, currency):
    whole, _, fraction = format(value, 'f').partition('.')
    amount = f"{whole}.{fraction.rstrip('0').ljust(2, '0')}"
    return f'{currency.symbol}{amount}' if currency.symbol else f'{currency.code} {amount}'


@lru_cache(maxsize=4)
def font_characters(path):
    with TTFont(path) as font:
        return frozenset(font.getBestCmap())


@lru_cache(maxsize=124)
def label_font(path, size):
    return ImageFont.truetype(path, size * 4)


def price_width(price, bold):
    # Measure the same font runs and fallback order used by the label template.
    weight = 'SemiBold' if bold else 'Regular'
    paths = tuple(ASSETS / 'fonts' / f'{family}-{weight}.ttf' for family in ('NotoSans', 'NotoSansBengali'))
    size = price['size']

    def font_for(character):
        return next((path for path in paths if ord(character) in font_characters(path)), None)

    width = 0
    for path, characters in groupby(price['text'], key=font_for):
        run = ''.join(characters)
        width += label_font(path, size).getlength(run) / 4 if path else len(run) * size
    return Decimal(str(width)) * MM_PER_PX


def retail_barcode(code):
    if re.fullmatch(r'[0-9]{12,13}', code):
        symbol = get('upca' if len(code) == 12 else 'ean13', code)
        if symbol.get_fullcode() == code:
            return symbol
    return None


def encode(code, auto_retail, width, height):
    if not re.fullmatch(r'[ -~]+', code):
        raise ValueError('The barcode or SKU must contain printable ASCII characters.')
    symbol = retail_barcode(code) if auto_retail else None
    if symbol is not None:
        return retail_symbol(symbol.build()[0], code, width, height)

    pattern = get('code128', code).build()[0]
    # Two to four print dots at 200 dpi, plus the mandatory quiet zone.
    bar_height = min(Decimal('18'), height - CODE_CAPTION_HEIGHT)
    if bar_height >= Decimal('6.35'):
        for module in map(Decimal, ('0.508', '0.381', '0.254')):
            quiet = max(QUIET_ZONE, 10 * module)
            symbol_width = len(pattern) * module + 2 * quiet
            if symbol_width <= width:
                return {
                    'width': symbol_width, 'height': bar_height, 'caption': code,
                    'bars': bar_rectangles(pattern, quiet, module, bar_height, bar_height),
                }
    raise ValueError('The Code 128 barcode does not fit. Choose a larger label or reduce the label text.')


def bar_rectangles(pattern, quiet, module, height, guard_height):
    return [
        {'x': quiet + match.start() * module, 'width': len(match.group()) * module,
         'height': guard_height if match.group()[0] == 'G' else height}
        for match in re.finditer(r'1+|G+', pattern)
    ]


def retail_symbol(pattern, code, width, height):
    ean = len(code) == 13
    left_modules, right_modules = (11, 7) if ean else (9, 9)
    # The outer and center guards extend below the main bars into the digit row.
    pattern = ''.join('G' if index in (0, 2, 46, 48, 92, 94) else bar
                      for index, bar in enumerate(pattern))
    for module in map(Decimal, ('0.330', '0.300', '0.264')):
        left = max(QUIET_ZONE, left_modules * module)
        right = max(QUIET_ZONE, right_modules * module)
        symbol_width = left + len(pattern) * module + right
        bar_height = Decimal('18.28') * module / Decimal('0.264')
        symbol_height = bar_height + 9 * module
        if symbol_width <= width and symbol_height <= height:
            groups = (
                ((Decimal('-5.5'), code[0]), (24, code[1:7]), (71, code[7:]))
                if ean else
                ((Decimal('-4.5'), code[0]), (Decimal('27.5'), code[1:6]),
                 (Decimal('67.5'), code[6:11]), (Decimal('99.5'), code[11]))
            )
            return {
                'width': symbol_width, 'height': symbol_height,
                'bars': bar_rectangles(pattern, left, module, bar_height, bar_height + 5 * module),
                'digits': [{'x': left + offset * module, 'text': digits} for offset, digits in groups],
                'digits_y': bar_height + 8 * module,
                'digits_size': module * 10,
            }
    name = 'EAN-13' if ean else 'UPC-A'
    raise ValueError(f'The {name} barcode does not fit. Choose a larger label or reduce the label text.')


def build_label(product, config, paper, company, date):
    promo_shown = config['show_promo_price'] and product.has_active_promo(date)
    raw = {
        'product_name': product.name if config['show_product_name'] else '',
        'business_name': company['name'] if config['show_business_name'] else '',
        'price': money(product.sale_price, product.currency) if config['show_price'] else '',
        'promo_price': money(product.promo_price, product.currency) if promo_shown else '',
    }
    label = {key: {'text': value, 'size': config[f'{key}_size']} for key, value in raw.items() if value}
    prices = [(label[key], key == 'promo_price') for key in ('price', 'promo_price') if key in label]
    width = paper.label_width - 2 * LABEL_PADDING
    if prices and sum(price_width(price, bold) for price, bold in prices) + (len(prices) - 1) * PRICE_GAP > width:
        raise ValueError('The price text does not fit. Reduce the price font sizes or choose a wider label.')
    price_size = max((price['size'] for price, _ in prices), default=0)
    text_height = sum(label.get(key, {}).get('size', 0) for key in ('product_name', 'business_name'))
    text_height = (text_height + price_size) * LINE_HEIGHT * MM_PER_PX
    label['price_size'] = price_size
    label['code'] = product.label_code
    label['barcode'] = encode(
        label['code'], config['auto_retail_barcodes'], width,
        paper.label_height - 2 * LABEL_PADDING - text_height,
    )
    return label


def print_document(items, products, config, paper):
    company = {'name': 'eManager', **getattr(settings, 'REPORT_COMPANY', {})}
    date = timezone.localdate()
    labels = []
    for item in items:
        product = products[item['product_id']]
        try:
            label = build_label(product, config, paper, company, date)
        except ValueError as exc:
            raise ValueError(f'{product.name} ({product.label_code}): {exc}') from exc
        labels.extend([label] * item['quantity'])
    return {
        'company': company, 'paper': paper, 'price_gap': PRICE_GAP, 'label_padding': LABEL_PADDING,
        'line_height': LINE_HEIGHT, 'caption_size': CAPTION_SIZE,
        'pages': [
            [{'label': label, **paper.position(slot)}
             for slot, label in enumerate(labels[start:start + paper.capacity])]
            for start in range(0, len(labels), paper.capacity)
        ],
    }
