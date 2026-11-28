from .branding import VIEW_LOGO, current_logo_revision, logo_url


def settings_navigation(request):
    user = request.user
    revision = current_logo_revision()
    return {
        'erp_logo_url': logo_url(revision),
        'erp_sidebar_logo_url': logo_url(revision, sidebar=True),
        'settings_access': {
            'trade_status': user.is_authenticated and user.has_perm('common.view_tradestatus'),
            'logo': user.is_authenticated and user.has_perm(VIEW_LOGO),
        },
    }
