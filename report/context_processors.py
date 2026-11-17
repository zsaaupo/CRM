from django.urls import reverse

from account.permissions import has_role_permissions
from warehouse.scope import has_warehouse_scope
from .catalog import REPORTS


PAGES = {
    ('purchase_pages', 'purchase-list'): ('purchase', 'list'),
    ('purchase_pages', 'purchase-detail'): ('purchase', 'detail'),
    ('sale_pages', 'sale-list'): ('sale', 'list'),
    ('sale_pages', 'sale-detail'): ('sale', 'detail'),
    ('warehouse_pages', 'transfer-list'): ('transfer', 'list'),
    ('warehouse_pages', 'transfer-detail'): ('transfer', 'detail'),
}


def report_actions(request):
    match = request.resolver_match
    page = PAGES.get((match.namespace, match.url_name)) if match else None
    if not page or not has_role_permissions(request.user, REPORTS[page[0]].permissions) or not has_warehouse_scope(request.user):
        return {'report_links': {}}
    kind, mode = page
    key = 'list_url_template' if mode == 'list' else 'detail_url'
    return {'report_links': {key: reverse(f'report:{kind}-pdf', args=[0 if mode == 'list' else match.kwargs['pk']])}}
