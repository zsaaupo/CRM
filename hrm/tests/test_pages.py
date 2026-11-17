from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client, TestCase
from django.urls import reverse

from hrm.catalog import RESOURCES
from hrm.models import Employee


class HRMPageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='hrm-pages')
        self.client.force_login(self.user)

    def grant(self, resource, *actions):
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label='hrm',
            codename__in=[f'{action}_{resource.model._meta.model_name}' for action in actions]))

    def test_pages_require_login_and_model_permissions_and_use_existing_api_forms(self):
        anonymous = Client()
        for resource in RESOURCES:
            with self.subTest(resource=resource.key):
                listing = reverse(f'hrm_pages:{resource.key}-list')
                adding = reverse(f'hrm_pages:add-{resource.key}')
                for url in [listing, adding]:
                    self.assertRedirects(anonymous.get(url), reverse('account_pages:login'))
                    self.assertEqual(self.client.get(url).status_code, 403)
                self.grant(resource, 'view')
                response = self.client.get(listing)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'data-api-url="{reverse(f"hrm:{resource.key}-list-view")}"')
                self.assertNotContains(response, 'class="primary-button compact-primary"')
                self.assertEqual(self.client.get(adding).status_code, 403)
                self.grant(resource, 'add')
                response = self.client.get(adding)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'action="{reverse(f"hrm:{resource.key}-list")}"')
                self.assertContains(response, f'data-success-url="{listing}"')
                self.assertContains(response, 'name="csrfmiddlewaretoken"')
                self.assertContains(response, '/static/inventory/js/catalog_form.js')
                self.assertNotIn('form', response.context)
                self.assertIn('no-store', response['Cache-Control'])

    def test_menu_lists_employees_first_and_only_employees_have_add_shortcut(self):
        for resource in RESOURCES:
            self.grant(resource, 'view', 'add')
        response = self.client.get(reverse('hrm_pages:employee-list'))
        html = response.content.decode()
        submenu = html.split('<div id="hrm-submenu"', 1)[1].split('</div>', 1)[0]
        previous = -1
        for resource in RESOURCES:
            position = submenu.index(f'href="{reverse(f"hrm_pages:{resource.key}-list")}"')
            self.assertGreater(position, previous)
            previous = position
            add_link = f'href="{reverse(f"hrm_pages:add-{resource.key}")}"'
            if resource.key == 'employee':
                self.assertIn(add_link, submenu)
            else:
                self.assertNotIn(add_link, submenu)
        self.assertContains(response, 'aria-controls="hrm-submenu" aria-expanded="true"')
        self.assertContains(response, 'data-employee-toggle aria-controls="hrm-employee-submenu" aria-expanded="true"')
        self.assertIn('<div id="hrm-employee-submenu"', submenu)
        self.assertIn('aria-current="page">Employee list', submenu)
        response = self.client.get(reverse('hrm_pages:add-employee'))
        self.assertContains(response, 'aria-current="page">Add employee')

    def test_group_grants_and_revocation_control_menu_and_pages(self):
        self.assertNotContains(self.client.get(reverse('dashboard')), 'data-hrm-toggle')
        role = Group.objects.create(name='HRM readers')
        role.permissions.add(Permission.objects.get(content_type__app_label='hrm', codename='view_employee'))
        self.user.groups.add(role)
        response = self.client.get(reverse('hrm_pages:employee-list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-hrm-toggle')
        self.assertNotContains(response, 'Department list')
        self.assertNotContains(response, '>Add employee</a>')
        role.permissions.clear()
        self.assertEqual(self.client.get(reverse('hrm_pages:employee-list')).status_code, 403)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'data-hrm-toggle')

    def test_add_permission_without_view_does_not_expose_list_destination(self):
        for resource in RESOURCES:
            self.grant(resource, 'add')
            self.assertEqual(self.client.get(reverse(f'hrm_pages:add-{resource.key}')).status_code, 403)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'data-hrm-toggle')

    def test_employee_form_has_api_options_photo_and_nullable_date_without_archive_controls(self):
        self.grant(RESOURCES[0], 'view', 'add')
        response = self.client.get(reverse('hrm_pages:add-employee'))
        for name in ['employee_code', 'full_name', 'image', 'work_email', 'work_phone', 'department',
                     'designation', 'employment_type', 'status', 'joining_date', 'leaving_date',
                     'reports_to', 'warehouse', 'workplace']:
            self.assertContains(response, f'name="{name}"')
        self.assertContains(response, 'data-options-url="/hrm/api/employees/options/"')
        self.assertContains(response, 'data-default="full-time"')
        self.assertContains(response, 'data-default="active"')
        self.assertContains(response, 'data-catalog-submit disabled')
        self.assertContains(response, 'data-nullable-field="true"')
        self.assertContains(response, 'accept="image/*"')
        for field in ['is_archived', 'archived_by', 'archived_at', 'create_by', 'user']:
            self.assertNotContains(response, f'name="{field}"')

    def test_edit_actions_and_employee_soft_delete_confirmation_are_permission_aware(self):
        employee = Employee.objects.create(employee_code='EMP-1', full_name='Employee')
        resource = RESOURCES[0]
        self.grant(resource, 'view')
        response = self.client.get(reverse('hrm_pages:employee-list'))
        self.assertEqual(len(response.context['columns']), len(resource.columns) + 1)
        self.assertContains(response, 'data-view-url-template="/hrm/employees/0/"')
        editing = reverse('hrm_pages:edit-employee', args=[employee.pk])
        self.assertEqual(self.client.get(editing).status_code, 403)
        self.grant(resource, 'change', 'delete')
        response = self.client.get(reverse('hrm_pages:employee-list'))
        self.assertContains(response, 'data-edit-url-template="/hrm/employees/0/edit/"')
        self.assertContains(response, 'data-delete-url-template="/hrm/api/employees/0/"')
        self.assertContains(response, 'The employee record will be retained.')
        self.assertNotContains(response, 'The party record')
        self.assertNotContains(response, 'Permanently delete')
        response = self.client.get(editing)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-detail-url="/hrm/api/employees/{employee.pk}/"')
        self.assertContains(response, f'data-options-url="/hrm/api/employees/{employee.pk}/options/"')
        self.assertContains(response, 'data-method="PATCH"')
        self.assertContains(response, 'data-success-url="/hrm/employees/"')
        employee.delete(actor='tester')
        self.assertEqual(self.client.get(editing).status_code, 404)

    def test_lookup_edit_pages_load_existing_records_and_lock_codes(self):
        for resource in RESOURCES[1:]:
            record = resource.model.objects.create(code='custom', name='Custom')
            self.grant(resource, 'view', 'change', 'delete')
            response = self.client.get(reverse(f'hrm_pages:edit-{resource.key}', args=[record.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['lock_code'])
            self.assertContains(response, f'action="{reverse(f"hrm:{resource.key}-update", args=[record.pk])}"')
            self.assertContains(response, 'data-method="PATCH"')
            listing = self.client.get(reverse(f'hrm_pages:{resource.key}-list'))
            self.assertContains(listing, 'Permanently delete')

    def test_login_link_control_requires_user_permission_and_protects_superusers(self):
        self.grant(RESOURCES[0], 'view', 'add', 'change')
        adding = reverse('hrm_pages:add-employee')
        self.assertNotContains(self.client.get(adding), 'name="user"')
        self.user.user_permissions.add(Permission.objects.get(content_type__app_label='auth', codename='change_user'))
        self.assertContains(self.client.get(adding), 'name="user"')
        root = get_user_model().objects.create_superuser(username='root')
        employee = Employee.objects.create(employee_code='ROOT', full_name='Root Employee', user=root)
        response = self.client.get(reverse('hrm_pages:edit-employee', args=[employee.pk]))
        self.assertNotContains(response, 'name="user"')

    def test_page_forms_create_through_csrf_protected_apis_then_list_records(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        for resource in RESOURCES:
            self.grant(resource, 'view', 'add')
            page = client.get(reverse(f'hrm_pages:add-{resource.key}'))
            payload = {'employee_code': 'EMP-PAGE', 'full_name': 'Page Employee',
                       'employment_type': 'full-time', 'status': 'active', 'joining_date': '2026-01-01',
                       'leaving_date': '', 'department': '', 'designation': '', 'reports_to': '', 'warehouse': ''}
            if resource.key != 'employee':
                payload = {'code': 'page-record', 'name': 'Page record', 'is_active': 'true'}
            create_url = page.context['create_api_url']
            self.assertEqual(client.post(create_url, payload).status_code, 403)
            response = client.post(create_url, payload, HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)
            self.assertEqual(response.status_code, 201, response.content)
            listing = client.get(page.context['list_url'])
            result = client.get(listing.context['list_api_url'], {'q': 'Page'}).json()
            self.assertEqual(result['count'], 1)
            self.assertEqual(result['results'][0]['create_by'], self.user.username)
