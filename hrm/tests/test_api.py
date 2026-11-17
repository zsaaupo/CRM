from io import BytesIO
from tempfile import TemporaryDirectory

from PIL import Image
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from hrm.models import Department, Designation, Employee, EmployeeStatus, EmploymentType
from hrm.serializers import EmployeeSerializer
from warehouse.models import RoleWarehouseAccess, WareHouse


RESOURCES = [
    (Department, 'department', 'department'),
    (Designation, 'designation', 'designation'),
    (EmploymentType, 'employment-type', 'employment_type'),
    (EmployeeStatus, 'employee-status', 'employee_status'),
    (Employee, 'employee', 'employee'),
]


class HRMAPITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='hr-operator')
        self.role = Group.objects.create(name='HR operators')
        self.user.groups.add(self.role)
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)

    def grant(self, model, *actions):
        self.role.permissions.add(*Permission.objects.filter(content_type__app_label='hrm',
            codename__in=[f'{action}_{model._meta.model_name}' for action in actions]))

    def payload(self, model, **changes):
        values = {'employee_code': 'EMP-100', 'full_name': 'Employee Name'} if model is Employee else {
            'code': 'custom', 'name': 'Custom record', 'description': 'HRM record'}
        return {**values, **changes}

    def create_record(self, model):
        return model.objects.create(**self.payload(model), create_by='original', modified_by='original')

    def test_all_resources_require_action_permissions_and_use_existing_response_structure(self):
        for model, name, key in RESOURCES:
            with self.subTest(model=model.__name__):
                url = reverse(f'hrm:{name}-list')
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url, self.payload(model), format='json').status_code, 403)
                self.grant(model, 'add')
                response = self.client.post(url, self.payload(model, id=999999, create_by='spoof', modified_by='spoof'), format='json')
                self.assertEqual(response.status_code, 201, response.data)
                record = response.data[key]
                self.assertEqual(record['create_by'], self.user.username)
                self.assertEqual(record['modified_by'], self.user.username)
                self.assertNotEqual(record['id'], 999999)
                detail = reverse(f'hrm:{name}-detail', args=[record['id']])
                update = reverse(f'hrm:{name}-update', args=[record['id']])
                self.assertEqual(self.client.get(detail).status_code, 403)
                self.assertEqual(self.client.patch(detail, {}, format='json').status_code, 403)
                self.assertEqual(self.client.delete(detail).status_code, 403)
                self.grant(model, 'view')
                self.assertEqual(self.client.get(detail).status_code, 200)
                self.grant(model, 'change')
                change = {'full_name': 'Updated'} if model is Employee else {'name': 'Updated'}
                for endpoint in [detail, update]:
                    response = self.client.patch(endpoint, {**change, 'create_by': 'spoof'}, format='json')
                    self.assertEqual(response.status_code, 200, response.data)
                    self.assertEqual(response.data['create_by'], self.user.username)
                    self.assertEqual(response.data['modified_by'], self.user.username)
                self.assertEqual(self.client.put(update, self.payload(model), format='json').status_code, 200)
                self.grant(model, 'delete')
                self.assertEqual(self.client.delete(detail).status_code, 204)
                self.assertEqual(self.client.get(detail).status_code, 404)
                self.assertFalse(model.objects.filter(pk=record['id']).exists())
                if model is Employee:
                    archived = Employee.all_objects.get(pk=record['id'])
                    self.assertEqual(archived.archived_by, self.user.username)

    def test_employee_archives_are_never_accessible_through_any_api_even_for_superusers(self):
        record = self.create_record(Employee)
        self.grant(Employee, 'view', 'change', 'delete', 'add')
        detail = reverse('hrm:employee-detail', args=[record.pk])
        update = reverse('hrm:employee-update', args=[record.pk])
        self.assertEqual(self.client.delete(detail).status_code, 204)
        for superuser in [False, True]:
            self.user.is_superuser = superuser
            self.user.save(update_fields=['is_superuser'])
            with self.subTest(superuser=superuser):
                for name in ['employee-list', 'employee-list-view']:
                    response = self.client.get(reverse(f'hrm:{name}'), {'is_archived': 'true', 'include_archived': 'true'})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.data['count'], 0)
                for method in ['get', 'put', 'patch', 'delete']:
                    response = getattr(self.client, method)(detail, self.payload(Employee, is_archived=False), format='json')
                    self.assertEqual(response.status_code, 404, response.data)
                self.assertEqual(self.client.patch(update, {'is_archived': False}, format='json').status_code, 404)
        archived = Employee.all_objects.get(pk=record.pk)
        self.assertTrue(archived.is_archived)
        self.assertEqual(archived.modified_by, self.user.username)
        self.assertIsNotNone(archived.archived_at)

    def test_archive_and_audit_fields_cannot_be_written(self):
        self.grant(Employee, 'add', 'change')
        url = reverse('hrm:employee-list')
        response = self.client.post(url, self.payload(Employee, is_archived=True, archived_by='spoof',
            archived_at='2026-01-01T00:00:00Z'), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        employee = Employee.objects.get(pk=response.data['employee']['id'])
        self.assertFalse(employee.is_archived)
        self.assertIsNone(employee.archived_at)
        self.assertIsNone(employee.archived_by)

    def test_archived_employee_code_and_login_cannot_be_reused(self):
        linked_user = get_user_model().objects.create_user(username='linked')
        record = Employee.objects.create(employee_code='EMP-100', full_name='Existing', user=linked_user)
        record.delete(actor='tester')
        self.grant(Employee, 'add')
        self.role.permissions.add(Permission.objects.get(content_type__app_label='auth', codename='change_user'))
        url = reverse('hrm:employee-list')
        response = self.client.post(url, self.payload(Employee, employee_code=' emp-100 '), format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('employee_code', response.data)
        response = self.client.post(url, self.payload(Employee, employee_code='EMP-200', user=linked_user.pk), format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('user', response.data)

    def test_employee_validates_dates_cycles_and_archived_manager_assignments(self):
        self.grant(Employee, 'add', 'change')
        manager = self.create_record(Employee)
        report = Employee.objects.create(employee_code='EMP-200', full_name='Report', reports_to=manager)
        update = reverse('hrm:employee-update', args=[manager.pk])
        response = self.client.patch(update, {'reports_to': report.pk}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('reports_to', response.data)
        manager.delete(actor='tester')
        response = self.client.post(reverse('hrm:employee-list'), self.payload(Employee, employee_code='EMP-300',
            reports_to=manager.pk), format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('reports_to', response.data)
        update = reverse('hrm:employee-update', args=[report.pk])
        self.assertEqual(self.client.patch(update, {'full_name': 'Report updated'}, format='json').status_code, 200)
        response = self.client.patch(update, {'joining_date': '2026-01-02', 'leaving_date': '2026-01-01'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('leaving_date', response.data)

    def test_lookup_deletion_is_protected_even_when_referencing_employee_is_archived(self):
        department = self.create_record(Department)
        designation = self.create_record(Designation)
        employee = Employee.objects.create(employee_code='EMP-100', full_name='Existing',
            department=department, designation=designation)
        for archived in [False, True]:
            if archived:
                employee.delete(actor='tester')
            for model, name, record in [(Department, 'department', department), (Designation, 'designation', designation),
                    (EmploymentType, 'employment-type', employee.employment_type),
                    (EmployeeStatus, 'employee-status', employee.status)]:
                self.grant(model, 'delete')
                with self.subTest(model=model.__name__, archived=archived):
                    response = self.client.delete(reverse(f'hrm:{name}-detail', args=[record.pk]))
                    self.assertEqual(response.status_code, 409, response.data)
                    self.assertTrue(model.objects.filter(pk=record.pk).exists())

    def test_lookup_validation_normalization_and_immutable_codes(self):
        for model, name, key in RESOURCES[:-1]:
            self.grant(model, 'add', 'change')
            code = 'new-status' if model is EmployeeStatus else ' New-Status '
            response = self.client.post(reverse(f'hrm:{name}-list'), self.payload(model, code=code, name=' New '), format='json')
            self.assertEqual(response.status_code, 201, response.data)
            record = response.data[key]
            self.assertEqual(record['code'], 'new-status')
            self.assertEqual(record['name'], 'New')
            update = reverse(f'hrm:{name}-update', args=[record['id']])
            self.assertEqual(self.client.patch(update, {'code': 'changed'}, format='json').status_code, 400)
        response = self.client.post(reverse('hrm:employee-status-list'), {'code': 'invalid slug', 'name': 'Invalid'}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_login_link_requires_user_change_permission_and_preserves_access(self):
        self.grant(Employee, 'add', 'change', 'delete')
        target = get_user_model().objects.create_user(username='linked-user')
        target_role = Group.objects.create(name='Existing user role')
        target.groups.add(target_role)
        warehouse = WareHouse.objects.create(name='UTT', code='UTT', address='Uttara', phone='123')
        access = RoleWarehouseAccess.objects.create(role=target_role, warehouse=warehouse)
        url = reverse('hrm:employee-list')
        payload = self.payload(Employee, user=target.pk, warehouse=warehouse.pk)
        self.assertEqual(self.client.post(url, payload, format='json').status_code, 403)
        self.role.permissions.add(Permission.objects.get(content_type__app_label='auth', codename='change_user'))
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        detail = reverse('hrm:employee-detail', args=[response.data['employee']['id']])
        self.role.permissions.remove(Permission.objects.get(content_type__app_label='auth', codename='change_user'))
        self.assertEqual(self.client.patch(detail, {'user': None}, format='json').status_code, 403)
        self.assertEqual(self.client.patch(detail, {'status': 'left'}, format='json').status_code, 200)
        self.assertEqual(self.client.delete(detail).status_code, 204)
        target.refresh_from_db()
        access.refresh_from_db()
        self.assertTrue(target.is_active)
        self.assertEqual(list(target.groups.values_list('pk', flat=True)), [target_role.pk])
        self.assertEqual(access.warehouse_id, warehouse.pk)

    def test_non_superuser_cannot_link_or_unlink_superuser_login(self):
        self.grant(Employee, 'add', 'change')
        self.role.permissions.add(Permission.objects.get(content_type__app_label='auth', codename='change_user'))
        root = get_user_model().objects.create_superuser(username='root')
        response = self.client.post(reverse('hrm:employee-list'), self.payload(Employee, user=root.pk), format='json')
        self.assertEqual(response.status_code, 403)
        record = Employee.objects.create(employee_code='ROOT', full_name='Root Employee', user=root)
        response = self.client.patch(reverse('hrm:employee-update', args=[record.pk]), {'user': None}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_csrf_anonymous_inactive_and_revoked_role_permissions(self):
        self.grant(Employee, 'add', 'view', 'change', 'delete')
        record = self.create_record(Employee)
        url = reverse('hrm:employee-list')
        detail = reverse('hrm:employee-detail', args=[record.pk])
        self.client.credentials()
        for method, endpoint in [('post', url), ('put', detail), ('patch', detail), ('delete', detail)]:
            self.assertEqual(getattr(self.client, method)(endpoint, self.payload(Employee), format='json').status_code, 403)
        self.assertEqual(APIClient().get(url).status_code, 403)
        self.role.permissions.clear()
        self.assertEqual(self.client.get(url).status_code, 403)
        self.grant(Employee, 'view')
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_list_search_filters_pagination_and_read_only_methods(self):
        self.grant(Employee, 'view')
        department = self.create_record(Department)
        Employee.objects.create(employee_code='MATCH', full_name='A Matched Person', department=department)
        Employee.objects.bulk_create([Employee(employee_code=f'EXTRA-{i}', full_name=f'Other {i:02}') for i in range(21)])
        url = reverse('hrm:employee-list-view')
        response = self.client.get(url)
        self.assertEqual(response.data['count'], 22)
        self.assertEqual(len(response.data['results']), 20)
        self.assertEqual(len(self.client.get(response.data['next']).data['results']), 2)
        self.assertEqual(self.client.get(url, {'q': ' matched '}).data['count'], 1)
        self.assertEqual(self.client.get(url, {'department': department.pk, 'status': 'active'}).data['count'], 1)
        self.assertEqual(self.client.get(url, {'warehouse': 'invalid'}).status_code, 400)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.client.head(url).status_code, 200)
        self.assertEqual(self.client.options(url).status_code, 200)
        for method in ['post', 'patch', 'put', 'delete']:
            self.assertEqual(getattr(self.client, method)(url, {}, format='json').status_code, 405)

    def test_photo_upload_and_invalid_image(self):
        self.grant(Employee, 'add', 'change')
        image = BytesIO()
        Image.new('RGB', (2, 2), 'blue').save(image, format='PNG')
        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            response = self.client.post(reverse('hrm:employee-list'), self.payload(Employee,
                image=SimpleUploadedFile('employee.png', image.getvalue(), content_type='image/png')), format='multipart')
            self.assertEqual(response.status_code, 201, response.data)
            employee = Employee.objects.get(pk=response.data['employee']['id'])
            self.assertTrue(employee.image.name.startswith('employee_pictures/'))
            response = self.client.patch(reverse('hrm:employee-update', args=[employee.pk]), {
                'image': SimpleUploadedFile('bad.png', b'not an image', content_type='image/png')}, format='multipart')
            self.assertEqual(response.status_code, 400)

    def test_stale_serializer_cannot_restore_an_employee_archived_after_validation(self):
        from rest_framework.exceptions import NotFound

        record = self.create_record(Employee)
        serializer = EmployeeSerializer(record, data={'full_name': 'Late update'}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        record.delete(actor='archiver')
        with self.assertRaises(NotFound):
            serializer.save(modified_by='late-editor')
        record.refresh_from_db()
        self.assertTrue(record.is_archived)
        self.assertEqual(record.full_name, 'Employee Name')
        self.assertEqual(record.archived_by, 'archiver')
