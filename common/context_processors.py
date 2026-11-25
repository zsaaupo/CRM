from django.utils import translation

from .models import Language, NotificationRecipient
from .settings_store import get_general


def settings_navigation(request):
    user = request.user
    return {'settings_access': {
        'trade_status': user.is_authenticated and user.has_perm('common.view_tradestatus'),
        'general': user.is_authenticated and user.has_perm('common.view_generalsetting'),
        'audit_logs': user.is_authenticated and user.has_perm('common.view_auditlog'),
        'tasks': user.is_authenticated,
        'manufacturing': user.is_authenticated and user.has_perm('manufacturing.view_productionorder'),
        'reports': user.is_authenticated and (
            user.is_superuser or user.has_perm('sale.view_salesorder') or user.has_perm('purchase.view_purchaseorder')
        ),
    }}


def general_settings(request):
    general = get_general()
    lang_code = translation.get_language() or 'en'
    short_code = lang_code.split('-')[0].lower()
    active_langs = list(Language.objects.filter(is_active=True).order_by('code').values('code', 'name', 'is_rtl'))
    if not active_langs:
        active_langs = [
            {'code': 'en', 'name': 'English', 'is_rtl': False},
            {'code': 'bn', 'name': 'Bengali', 'is_rtl': False},
            {'code': 'ar', 'name': 'Arabic', 'is_rtl': True},
        ]
    lang_obj = next((l for l in active_langs if l['code'].lower() in (lang_code.lower(), short_code)), None)
    is_rtl = bool(getattr(general, 'rtl_layout', False) or (lang_obj and lang_obj['is_rtl']))
    unread = 0
    if getattr(request, 'user', None) and request.user.is_authenticated:
        unread = NotificationRecipient.objects.filter(user=request.user, read_at__isnull=True).count()
    return {
        'general_setting': general,
        'active_language': lang_code,
        'available_languages': active_langs,
        'is_rtl': is_rtl,
        'unread_notifications': unread,
    }
