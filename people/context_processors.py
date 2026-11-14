from django.urls import reverse


def people_navigation(request):
    user = request.user
    if not user.is_authenticated or not user.has_perm('people.view_party'):
        return {'people_menu': []}
    can_add = user.has_perm('people.add_party')
    return {'people_menu': [
        {'role': role, 'label': f'{role.capitalize()} list',
         'add_label': f'Add {role}', 'can_add': can_add,
         'list_url': reverse(f'people_pages:{role}-list'),
         'add_url': reverse(f'people_pages:add-{role}')}
        for role in ('supplier', 'customer')
    ]}
