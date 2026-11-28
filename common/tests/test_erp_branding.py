from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image
from rest_framework.test import APIClient

from audit_log.models import AuditLog
from common.branding import LOGO_MAX_BYTES
from common.models import ERPBranding
from report.rendering import render_pdf
from report.demo import demo_document


class ERPBrandingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.manager = users.create_user(username='branding-manager')
        cls.viewer = users.create_user(username='branding-viewer')
        cls.employee = users.create_user(username='branding-employee')
        permissions = Permission.objects.filter(content_type__app_label='common', content_type__model='erpbranding')
        cls.manager.user_permissions.set(permissions)
        cls.viewer.user_permissions.set(permissions.filter(codename='view_erpbranding'))

    def setUp(self):
        self.page = reverse('common_pages:logo-settings')
        self.api = reverse('common:logo-settings')
        self.client = APIClient(enforce_csrf_checks=True)

    def sign_in(self, user):
        self.client.force_login(user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)

    @staticmethod
    def logo(color=(20, 80, 110, 128), size=(120, 60)):
        data = BytesIO()
        Image.new('RGBA', size, color).save(data, format='PNG')
        return SimpleUploadedFile('company.png', data.getvalue(), content_type='image/png')

    def upload(self, logo=None):
        return self.client.patch(self.api, {'logo': logo if logo is not None else self.logo()}, format='multipart')

    def test_default_logo_and_permissions(self):
        login = self.client.get(reverse('account_pages:login'))
        self.assertContains(login, 'data-erp-logo-default aria-hidden="true"><i></i><i></i><i></i>')
        self.assertContains(login, '<span>eManager</span>')
        self.assertEqual(self.client.get(self.page).status_code, 302)
        self.assertEqual(self.client.get(self.api).status_code, 403)

        self.sign_in(self.employee)
        self.assertEqual(self.client.get(self.page).status_code, 403)
        dashboard = self.client.get(reverse('dashboard'))
        self.assertNotContains(dashboard, f'href="{self.page}"')
        self.assertNotContains(dashboard, '<span>eManager</span>')
        self.assertNotContains(dashboard, 'Enterprise workspace')
        self.assertContains(dashboard, 'data-erp-logo-default aria-hidden="true"><i></i><i></i><i></i>')
        self.assertContains(dashboard, 'data-sidebar-close')

        self.sign_in(self.viewer)
        listing = self.client.get(self.page)
        self.assertContains(listing, f'data-api-url="{self.api}"')
        self.assertContains(listing, 'aria-current="page">Logo')
        self.assertNotContains(listing, 'Trade status setting')
        self.assertNotContains(listing, 'data-logo-form')
        self.assertEqual(self.client.get(self.api).data, {'is_custom': False, 'logo_url': None})
        self.assertEqual(self.upload().status_code, 403)
        self.assertEqual(self.client.delete(self.api).status_code, 403)
        self.assertFalse(ERPBranding.objects.exists())

    def test_upload_replacement_and_reset_update_shared_brand_and_audit(self):
        self.sign_in(self.manager)
        self.assertContains(self.client.get(self.page), 'data-logo-form')
        self.client.credentials()
        self.assertEqual(self.upload().status_code, 403)
        self.sign_in(self.manager)

        uploaded = self.upload()
        self.assertEqual(uploaded.status_code, 200, uploaded.data)
        self.assertTrue(uploaded.data['is_custom'])
        first = uploaded.data['logo_url']
        anon = APIClient()
        image = anon.get(first)
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image['Content-Type'], 'image/png')
        self.assertIn('immutable', image['Cache-Control'])
        self.assertEqual(image['X-Content-Type-Options'], 'nosniff')
        with Image.open(BytesIO(b''.join(image.streaming_content))) as logo:
            self.assertEqual(logo.getpixel((0, 0)), (20, 80, 110, 128))
        self.assertContains(anon.get(reverse('account_pages:login')), f'src="{first}"')
        self.assertContains(self.client.get(reverse('dashboard')), f'src="{first}"')

        replaced = self.upload(self.logo(color=(80, 20, 110, 255)))
        self.assertEqual(replaced.status_code, 200)
        second = replaced.data['logo_url']
        self.assertNotEqual(second, first)
        self.assertEqual(anon.get(first).status_code, 404)
        self.assertContains(self.client.get(reverse('dashboard')), f'src="{second}"')
        self.assertEqual(ERPBranding.objects.count(), 1)

        self.client.credentials()
        self.assertEqual(self.client.delete(self.api).status_code, 403)
        self.sign_in(self.manager)
        self.assertEqual(self.client.delete(self.api).data, {'is_custom': False, 'logo_url': None})
        self.assertEqual(self.client.delete(self.api).status_code, 200)
        self.assertEqual(anon.get(second).status_code, 404)
        login = anon.get(reverse('account_pages:login'))
        self.assertContains(login, 'data-erp-logo-default aria-hidden="true"><i></i><i></i><i></i>')
        self.assertNotContains(login, f'src="{second}"')

        events = AuditLog.objects.filter(resource='common.erpbranding').order_by('pk')
        self.assertEqual(list(events.values_list('action', 'actor')), [
            ('logo_uploaded', self.manager.username), ('logo_uploaded', self.manager.username), ('logo_reset', self.manager.username),
        ])

    def test_invalid_uploads_preserve_the_current_logo(self):
        self.sign_in(self.manager)
        current = self.upload().data
        for upload in (
            SimpleUploadedFile('invalid.png', b'<svg></svg>', content_type='image/png'),
            SimpleUploadedFile('large.png', b'x' * (LOGO_MAX_BYTES + 1), content_type='image/png'),
            self.logo(size=(4097, 1)),
        ):
            with self.subTest(upload=upload.name):
                response = self.upload(upload)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn('logo', response.data)
                self.assertEqual(self.client.get(self.api).data, current)

    def test_uploaded_logo_reaches_branded_pdf_rendering(self):
        self.sign_in(self.manager)
        response = self.upload()
        image = b''.join(self.client.get(response.data['logo_url']).streaming_content)
        document = demo_document('sale')

        def html(*, string, url_fetcher, **kwargs):
            self.assertIn('src="report-asset:///logo"', string)
            resource = url_fetcher('report-asset:///logo')
            self.assertEqual(resource['mime_type'], 'image/png')
            self.assertEqual(resource['file_obj'].read(), image)
            return SimpleNamespace(write_pdf=lambda **kwargs: b'%PDF')

        with patch('report.rendering.pdf_engine', return_value=(None, html, object)):
            self.assertEqual(render_pdf(document, stylesheet=''), b'%PDF')
        self.assertNotIn('has_custom_logo', document['company'])
