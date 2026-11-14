from io import BytesIO
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
from rest_framework.test import APIClient

from people.models import Party


class PartyImageTests(TestCase):
    def setUp(self):
        self.enterContext(override_settings(MEDIA_ROOT=self.enterContext(TemporaryDirectory())))
        self.user = get_user_model().objects.create_superuser(username='party-image-admin', password=None)
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)
        self.url = reverse('people:party-list')

    def image(self, name='party.png', color='blue'):
        data = BytesIO()
        Image.new('RGB', (2, 2), color).save(data, 'PNG')
        return SimpleUploadedFile(name, data.getvalue(), content_type='image/png')

    def test_uploads_are_stored_under_people_picture_and_returned_by_read_apis(self):
        for role in ['supplier', 'customer']:
            response = self.client.post(self.url, {'code': role, 'name': role.capitalize(),
                f'is_{role}': True, 'image': self.image(name=f'{role}.png')}, format='multipart')
            self.assertEqual(response.status_code, 201, response.data)
            data = response.data['party']
            self.assertEqual(data['image'], f'http://testserver/media/people_picture/{role}.png')
            party = Party.objects.get(pk=data['id'])
            self.assertEqual(party.image.name, f'people_picture/{role}.png')
            self.assertTrue(party.image.storage.exists(party.image.name))
            with party.image.storage.open(party.image.name, 'rb') as stored:
                self.assertEqual(Image.open(stored).size, (2, 2))
            detail = self.client.get(reverse('people:party-detail', args=[party.pk]))
            self.assertEqual(detail.data['image'], data['image'])
            listing = self.client.get(reverse('people:party-list-view'), {f'is_{role}': 'true'})
            self.assertEqual(listing.data['results'][0]['image'], data['image'])

    def test_image_is_optional_and_can_be_replaced_kept_and_cleared(self):
        response = self.client.post(self.url, {'code': 'SUP', 'name': 'Supplier', 'is_supplier': True}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data['party']['image'])
        pk = response.data['party']['id']
        update = reverse('people:party-update', args=[pk])
        response = self.client.patch(update, {'image': self.image()}, format='multipart')
        self.assertEqual(response.status_code, 200, response.data)
        image_url = response.data['image']
        response = self.client.patch(update, {'name': 'Renamed'}, format='json')
        self.assertEqual(response.data['image'], image_url)
        response = self.client.patch(update, {'image': self.image('replacement.png', 'green')}, format='multipart')
        self.assertTrue(response.data['image'].endswith('/people_picture/replacement.png'))
        response = self.client.patch(update, {'image': None}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data['image'])

    def test_invalid_images_and_json_filename_strings_are_rejected(self):
        payload = {'code': 'SUP', 'name': 'Supplier', 'is_supplier': True}
        invalid = SimpleUploadedFile('fake.png', b'not an image', content_type='image/png')
        response = self.client.post(self.url, {**payload, 'image': invalid}, format='multipart')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('image', response.data)
        self.assertFalse(Party.objects.exists())
        response = self.client.post(self.url, {**payload, 'image': 'party.png'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('image', response.data)

    def test_filename_collisions_do_not_overwrite_another_party_image(self):
        names = []
        for code, color in [('SUP-1', 'blue'), ('SUP-2', 'green')]:
            response = self.client.post(self.url, {'code': code, 'name': code, 'is_supplier': True,
                                                  'image': self.image(color=color)}, format='multipart')
            self.assertEqual(response.status_code, 201, response.data)
            party = Party.objects.get(pk=response.data['party']['id'])
            names.append(party.image.name)
            self.assertTrue(party.image.storage.exists(party.image.name))
        self.assertNotEqual(*names)

    def test_soft_deletion_preserves_uploaded_file_and_image_reference(self):
        response = self.client.post(self.url, {'code': 'SUP', 'name': 'Supplier', 'is_supplier': True,
                                              'image': self.image()}, format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        pk = response.data['party']['id']
        detail = reverse('people:party-detail', args=[pk])
        self.assertEqual(self.client.delete(detail).status_code, 204)
        deleted = Party.all_objects.get(pk=pk)
        self.assertTrue(deleted.is_deleted)
        self.assertEqual(deleted.image.name, 'people_picture/party.png')
        self.assertTrue(deleted.image.storage.exists(deleted.image.name))
        self.assertEqual(self.client.get(detail).status_code, 404)

    def test_supplier_and_customer_pages_connect_upload_display_and_replacement(self):
        for role in ('supplier', 'customer'):
            with self.subTest(role=role):
                adding = self.client.get(reverse(f'people_pages:add-{role}'))
                self.assertContains(adding, 'name="image" type="file"')
                response = self.client.post(adding.context['create_api_url'], {
                    'code': f'PIC-{role}', 'name': f'Picture {role}', f'is_{role}': True,
                    'image': self.image(name=f'{role}.png'),
                }, format='multipart')
                self.assertEqual(response.status_code, 201, response.data)
                pk = response.data['party']['id']
                image_url = response.data['party']['image']
                listing = self.client.get(adding.context['list_url'])
                name_column = next(column for column in listing.context['columns'] if column['key'] == 'name')
                self.assertEqual(name_column['type'], 'identity')
                records = self.client.get(listing.context['list_api_url']).data['results']
                self.assertEqual(next(row for row in records if row['id'] == pk)['image'], image_url)
                editing = self.client.get(reverse(f'people_pages:edit-{role}', args=[pk]))
                self.assertContains(editing, 'data-current-image-preview')
                self.assertEqual(self.client.get(editing.context['detail_api_url']).data['image'], image_url)
                replaced = self.client.patch(editing.context['create_api_url'], {
                    'image': self.image(name=f'{role}-updated.png', color='green'),
                }, format='multipart')
                self.assertEqual(replaced.status_code, 200, replaced.data)
                self.assertTrue(replaced.data['image'].endswith(f'/people_picture/{role}-updated.png'))
                details = self.client.get(reverse(f'people_pages:{role}-detail', args=[pk]))
                self.assertContains(details, 'data-record-image-wrapper hidden')
                self.assertContains(details, 'data-record-image')
                self.assertEqual(self.client.get(details.context['detail_api_url']).data['image'], replaced.data['image'])
