from django.urls import reverse


def people_navigation(request):
    user = request.user
    if not user.is_authenticated:
        return {'people_menu': [], 'lead_access': {}}

    people_menu = []
    if user.has_perm('people.view_party'):
        can_add_party = user.has_perm('people.add_party')
        people_menu = [
            {'role': role, 'label': f'{role.capitalize()} list',
             'add_label': f'Add {role}', 'can_add': can_add_party,
             'list_url': reverse(f'people_pages:{role}-list'),
             'add_url': reverse(f'people_pages:add-{role}')}
            for role in ('supplier', 'customer')
        ]

    can_view_lead = user.has_perm('people.view_lead')
    return {
        'people_menu': people_menu,
        'lead_access': {'view': can_view_lead, 'add': can_view_lead and user.has_perm('people.add_lead')},
    }
