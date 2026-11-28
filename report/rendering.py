"""Render the shared Django print template with WeasyPrint."""
from functools import lru_cache
from io import BytesIO
import logging
import mimetypes
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

from django.conf import settings
from django.template.loader import render_to_string
from rest_framework.exceptions import APIException

from common.branding import logo_png


logger = logging.getLogger(__name__)
ASSETS = Path(__file__).resolve().parent / 'static' / 'report'
_dll_handles = []


class ReportUnavailable(APIException):
    status_code = 503
    default_detail = 'PDF generation is unavailable. Check the report renderer configuration and try again.'
    default_code = 'report_unavailable'


@lru_cache(maxsize=1)
def pdf_engine():
    if os.name == 'nt':
        directories = getattr(settings, 'REPORT_WEASYPRINT_DLL_DIRECTORIES', [
            settings.BASE_DIR / '.runtime' / 'weasyprint' / 'onedir' / 'weasyprint' / '_internal'])
        for directory in directories:
            path = Path(directory)
            if path.is_dir():
                _dll_handles.append(os.add_dll_directory(str(path)))
                font_config = path / 'etc' / 'fonts'
                if font_config.is_dir():
                    os.environ.setdefault('FONTCONFIG_PATH', str(font_config))
    from weasyprint import CSS, HTML
    from weasyprint.text.fonts import FontConfiguration
    return CSS, HTML, FontConfiguration


def asset_fetcher(company, *, uploaded_logo=None):
    def fetch(url):
        parsed = urlsplit(url)
        if parsed.scheme != 'report-asset' or parsed.netloc:
            raise ValueError('Only local report assets are allowed.')
        name = unquote(parsed.path).lstrip('/')
        if name == 'logo':
            if uploaded_logo:
                return {'file_obj': BytesIO(uploaded_logo), 'mime_type': 'image/png', 'redirected_url': url}
            path = Path(company['logo_path']).resolve()
            if path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
                raise ValueError('Use a PNG, JPEG or WebP report logo.')
        else:
            path = (ASSETS / name).resolve()
            if not path.is_relative_to(ASSETS.resolve()):
                raise ValueError('Invalid report asset path.')
        return {'file_obj': BytesIO(path.read_bytes()), 'mime_type': mimetypes.guess_type(path.name)[0], 'redirected_url': url}
    return fetch


def render_pdf(document, template_name='report/document.html', *, stylesheet='document.css'):
    try:
        CSS, HTML, FontConfiguration = pdf_engine()
        fonts = FontConfiguration()
        company = document['company']
        uploaded_logo = logo_png() if 'logo_path' in company else None
        if uploaded_logo:
            document = {**document, 'company': {**company, 'has_custom_logo': True}}
        fetch = asset_fetcher(company, uploaded_logo=uploaded_logo)
        stylesheets = []
        if stylesheet:
            stylesheets.append(CSS(string=(ASSETS / stylesheet).read_text(encoding='utf-8'),
                                   base_url='report-asset:///', url_fetcher=fetch, font_config=fonts))
        html = render_to_string(template_name, {'report': document})
        return HTML(string=html, base_url='report-asset:///', url_fetcher=fetch).write_pdf(
            stylesheets=stylesheets, font_config=fonts, pdf_tags=True)
    except (ImportError, OSError, ValueError, RuntimeError):
        logger.exception('Could not render the report PDF.')
        raise ReportUnavailable() from None
