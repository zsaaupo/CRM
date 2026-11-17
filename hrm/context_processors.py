from django.urls import reverse

from .catalog import RESOURCES


def hrm_navigation(request):
    if not request.user.is_authenticated:
        return {'hrm_menu': []}
    return {'hrm_menu': [
        {
            'key': resource.key,
            'label': f'{resource.singular.capitalize()} list',
            'list_url': reverse(f'hrm_pages:{resource.key}-list'),
            'add_url': reverse(f'hrm_pages:add-{resource.key}'),
            'can_add': request.user.has_perm(resource.permission('add')),
        }
        for resource in RESOURCES if request.user.has_perm(resource.permission('view'))
    ]}
