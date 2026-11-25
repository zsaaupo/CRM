from django.urls import reverse

from .catalog import RESOURCES


def inventory_navigation(request):
    if not request.user.is_authenticated:
        return {'inventory_menu': []}
    menu = []
    for resource in RESOURCES:
        if request.user.has_perm(resource.permission('view')):
            menu.append({
                'key': resource.key,
                'label': f'{resource.singular.capitalize()} list',
                'list_url': reverse(f'inventory_pages:{resource.key}-list'),
                'add_url': reverse(f'inventory_pages:add-{resource.key}'),
                'can_add': request.user.has_perm(resource.permission('add')),
            })
    if request.user.has_perm('inventory.view_product'):
        menu.append({
            'key': 'print-barcode',
            'label': 'Print barcode',
            'list_url': reverse('inventory_pages:print-barcode'),
            'add_url': '',
            'can_add': False,
        })
    return {'inventory_menu': menu}
