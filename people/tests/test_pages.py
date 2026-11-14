from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from people.models import Party


class PartyPageInputs(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.fields = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('input', 'textarea', 'select') and attrs.get('name'):
            self.fields[attrs['name']] = attrs


class PartyPageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='people-page-operator')
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.supplier = Party.objects.create(code='SUP-1', name='Supplier', is_supplier=True)
        self.customer = Party.objects.create(code='CUST-1', name='Customer', is_customer=True)
        self.both = Party.objects.create(code='BOTH', name='Both roles', is_supplier=True, is_customer=True)
        deleted = Party.objects.create(code='DELETED', name='Deleted', is_supplier=True, is_customer=True)
        deleted.delete(actor='original-deleter')

    def grant(self, *actions):
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label='people',
                                      codename__in=[f'{action}_party' for action in actions]))

    def pages(self, role):
        pk = getattr(self, role).pk
        return {
            'list': reverse(f'people_pages:{role}-list'),
            'add': reverse(f'people_pages:add-{role}'),
            'edit': reverse(f'people_pages:edit-{role}', args=[pk]),
            'detail': reverse(f'people_pages:{role}-detail', args=[pk]),
        }

    def csrf(self):
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)

    def test_pages_require_login_and_action_permissions(self):
        for role in ('supplier', 'customer'):
            for url in self.pages(role).values():
                self.assertRedirects(Client().get(url), reverse('account_pages:login'))
                self.assertEqual(self.client.get(url).status_code, 403)
        self.grant('view')
        for role in ('supplier', 'customer'):
            pages = self.pages(role)
            for mode in ('list', 'detail'):
                response = self.client.get(pages[mode])
                self.assertEqual(response.status_code, 200)
                self.assertIn('no-store', response['Cache-Control'])
            for mode in ('add', 'edit'):
                self.assertEqual(self.client.get(pages[mode]).status_code, 403)
            listing = self.client.get(pages['list'])
            self.assertNotIn('edit_url_template', listing.context)
            self.assertNotIn('delete_url_template', listing.context)
            self.assertNotContains(listing, 'data-catalog-delete-dialog')
            self.assertNotContains(listing, f'href="{pages["add"]}"')
        self.grant('add', 'change')
        for role in ('supplier', 'customer'):
            for url in self.pages(role).values():
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_sidebar_has_exact_requested_order_and_active_list_or_add_state(self):
        self.assertNotContains(self.client.get(reverse('dashboard')), 'data-people-toggle')
        self.grant('add')
        self.assertNotContains(self.client.get(reverse('dashboard')), 'data-people-toggle')
        self.assertEqual(self.client.get(self.pages('supplier')['add']).status_code, 403)
        self.grant('view', 'change')
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'data-people-toggle')
        submenu = response.content.decode().split('<div id="people-submenu"', 1)[1].split('</div>', 1)[0]
        positions = []
        for role in ('supplier', 'customer'):
            positions += [submenu.index(f'href="{self.pages(role)[mode]}"') for mode in ('list', 'add')]
        self.assertEqual(positions, sorted(positions))
        for role in ('supplier', 'customer'):
            for mode, url in self.pages(role).items():
                page = self.client.get(url)
                self.assertContains(page, 'aria-controls="people-submenu" aria-expanded="true"')
                label = f'Add {role}' if mode == 'add' else f'{role.capitalize()} list'
                self.assertContains(page, f'aria-current="page">{label}')

    def test_lists_filter_roles_include_dual_roles_and_preserve_pagination_filter(self):
        self.grant('view')
        for role in ('supplier', 'customer'):
            listing = self.client.get(self.pages(role)['list'])
            api = listing.context['list_api_url']
            self.assertEqual(api, reverse('people:party-list-view') + f'?is_{role}=true')
            result = self.client.get(api).data
            self.assertEqual(result['count'], 2)
            self.assertEqual({row['id'] for row in result['results']}, {getattr(self, role).pk, self.both.pk})
            self.assertContains(listing, '/static/inventory/js/catalog_list.js')
            self.assertContains(listing, f'data-view-url-template="/people/{role}s/0/"')
        Party.objects.bulk_create([Party(code=f'EXTRA-{i}', name=f'Extra {i:02}', is_supplier=True) for i in range(21)])
        api = self.client.get(self.pages('supplier')['list']).context['list_api_url'] + '&q=Extra'
        first = self.client.get(api).data
        self.assertEqual(first['count'], 21)
        self.assertEqual(len(first['results']), 20)
        self.assertIn('is_supplier=true', first['next'])
        self.assertIn('q=Extra', first['next'])
        self.assertEqual(len(self.client.get(first['next']).data['results']), 1)

    def test_forms_use_api_urls_locked_primary_roles_and_all_business_fields(self):
        self.grant('view', 'add', 'change')
        expected_fields = {'code', 'name', 'image', 'is_customer', 'is_supplier', 'contact_person', 'phone',
                           'alternate_phone', 'email', 'website', 'tax_number', 'address', 'city',
                           'state', 'postal_code', 'country', 'notes', 'is_active'}
        for role in ('supplier', 'customer'):
            response = self.client.get(self.pages(role)['add'])
            fields = PartyPageInputs(response.content.decode()).fields
            self.assertEqual(set(fields) - {'csrfmiddlewaretoken'}, expected_fields)
            self.assertNotIn('form', response.context)
            self.assertContains(response, 'action="/people/api/parties/"')
            self.assertContains(response, f'data-success-url="{self.pages(role)["list"]}"')
            self.assertContains(response, '/static/inventory/js/catalog_form.js')
            self.assertIn('required', fields['code'])
            self.assertIn('required', fields['name'])
            self.assertEqual(fields['code']['maxlength'], '40')
            self.assertEqual(fields['email']['type'], 'email')
            self.assertEqual(fields['phone']['type'], 'tel')
            self.assertEqual(fields['image']['type'], 'file')
            self.assertEqual(fields['image']['accept'], 'image/*')
            self.assertNotIn('required', fields['image'])
            self.assertContains(response, 'enctype="multipart/form-data"')
            primary = fields[f'is_{role}']
            self.assertIn('checked', primary)
            self.assertIn('disabled', primary)
            self.assertEqual(primary['data-locked'], 'true')
            other = fields['is_customer' if role == 'supplier' else 'is_supplier']
            self.assertNotIn('checked', other)
            self.assertNotIn('disabled', other)
            edit = self.client.get(self.pages(role)['edit'])
            pk = getattr(self, role).pk
            self.assertContains(edit, f'data-detail-url="/people/api/parties/{pk}/?is_{role}=true"')
            self.assertContains(edit, f'action="/people/api/parties/{pk}/update/?is_{role}=true"')
            self.assertContains(edit, 'data-method="PATCH"')
            self.assertContains(edit, 'data-catalog-submit disabled')
            self.assertContains(edit, 'data-current-image hidden')
            self.assertContains(edit, 'data-current-image-preview')
            self.assertNotContains(edit, f'value="{getattr(self, role).name}"')

    def test_create_edit_detail_and_soft_delete_roundtrip_from_both_page_contexts(self):
        self.grant('view', 'add', 'change', 'delete')
        for role in ('supplier', 'customer'):
            with self.subTest(role=role):
                add = self.client.get(self.pages(role)['add'])
                self.csrf()
                payload = {'code': f'NEW-{role}', 'name': f'New {role}', f'is_{role}': True,
                           'is_customer' if role == 'supplier' else 'is_supplier': False, 'phone': '+8801700000000'}
                response = self.client.post(add.context['create_api_url'], payload, format='json')
                self.assertEqual(response.status_code, 201, response.data)
                pk = response.data['party']['id']
                edit = self.client.get(reverse(f'people_pages:edit-{role}', args=[pk]))
                self.assertEqual(self.client.get(edit.context['detail_api_url']).data['name'], f'New {role}')
                updated = self.client.patch(edit.context['create_api_url'], {'name': 'Changed', 'is_active': False}, format='json')
                self.assertEqual(updated.status_code, 200, updated.data)
                self.assertEqual(updated.data['name'], 'Changed')
                self.assertEqual(self.client.get(edit.context['list_url']).status_code, 200)
                detail = self.client.get(reverse(f'people_pages:{role}-detail', args=[pk]))
                self.assertContains(detail, f'data-api-url="/people/api/parties/{pk}/?is_{role}=true"')
                self.assertContains(detail, 'data-detail-field="contact_person"')
                self.assertContains(detail, '/static/people/js/party_detail.js')
                listing = self.client.get(self.pages(role)['list'])
                self.assertContains(listing, f'data-edit-url-template="/people/{role}s/0/edit/"')
                self.assertContains(listing, 'The party record will be retained.')
                self.assertNotContains(listing, 'Permanently delete')
                delete_url = listing.context['delete_url_template'].replace('/0/', f'/{pk}/')
                self.client.credentials()
                self.assertEqual(self.client.delete(delete_url).status_code, 403)
                self.csrf()
                self.assertEqual(self.client.delete(delete_url).status_code, 204)
                self.assertTrue(Party.all_objects.get(pk=pk).is_deleted)
                self.assertEqual(Party.all_objects.get(pk=pk).deleted_by, self.user.username)
                self.assertEqual(self.client.get(detail.context['detail_api_url']).status_code, 404)

    def test_role_scoped_detail_update_and_delete_cannot_target_the_other_list(self):
        self.grant('view', 'change', 'delete')
        self.csrf()
        for role, wrong_record in [('supplier', self.customer), ('customer', self.supplier)]:
            query = f'?is_{role}=true'
            detail = reverse('people:party-detail', args=[wrong_record.pk]) + query
            update = reverse('people:party-update', args=[wrong_record.pk]) + query
            self.assertEqual(self.client.get(detail).status_code, 404)
            self.assertEqual(self.client.patch(update, {'name': 'Changed'}, format='json').status_code, 404)
            self.assertEqual(self.client.delete(detail).status_code, 404)
            self.assertTrue(Party.objects.filter(pk=wrong_record.pk).exists())
        both_detail = reverse('people:party-detail', args=[self.both.pk])
        for role in ('supplier', 'customer'):
            self.assertEqual(self.client.get(both_detail + f'?is_{role}=true').status_code, 200)
        self.assertEqual(self.client.delete(both_detail + '?is_supplier=true').status_code, 204)
        for role in ('supplier', 'customer'):
            data = self.client.get(self.client.get(self.pages(role)['list']).context['list_api_url']).data
            self.assertNotIn(self.both.pk, [row['id'] for row in data['results']])

    def test_group_revocation_and_missing_or_deleted_records(self):
        group = Group.objects.create(name='People readers')
        group.permissions.add(Permission.objects.get(content_type__app_label='people', codename='view_party'))
        self.user.groups.add(group)
        for role in ('supplier', 'customer'):
            self.assertEqual(self.client.get(self.pages(role)['list']).status_code, 200)
            self.assertEqual(self.client.get(self.pages(role)['detail']).status_code, 200)
        missing = reverse('people_pages:supplier-detail', args=[999999])
        page = self.client.get(missing)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(self.client.get(page.context['detail_api_url']).status_code, 404)
        group.permissions.clear()
        for role in ('supplier', 'customer'):
            self.assertEqual(self.client.get(self.pages(role)['list']).status_code, 403)
            self.assertEqual(self.client.get(self.pages(role)['detail']).status_code, 403)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'data-people-toggle')
