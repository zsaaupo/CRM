from .settings_store import get_general


def settings_navigation(request):
    user = request.user
    return {'settings_access': {
        'trade_status': user.is_authenticated and user.has_perm('common.view_tradestatus'),
    }}


def general_settings(request):
    return {'general_setting': get_general()}
