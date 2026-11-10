from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .models import Account


User = get_user_model()


class AssignRolePageTests(TestCase):
    def setUp(self):
        self.caller = User.objects.create_user(username='manager@example.com')
        self.target = User.objects.create_user(username='member@example.com')
        self.old = Group.objects.create(name='Teachers')
        self.new = Group.objects.create(name='Sales manager')
        self.target.groups.add(self.old)
        self.url = reverse('account_pages:assign-role', args=[self.target.pk])
        self.client = APIClient(enforce_csrf_checks=True)

    def test_page_requires_login_and_unknown_users_return_404(self):
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse('account_pages:login'), fetch_redirect_response=False)
        self.client.force_login(self.caller)
        response = self.client.get(reverse('account_pages:assign-role', args=[self.target.pk + 999]))
        self.assertEqual(response.status_code, 404)

    def test_page_shows_target_current_role_and_single_select(self):
        Account.objects.create(user=self.target, full_name='<script>unsafe</script>',
                               email='member@example.com', phone_number='')
        self.client.force_login(self.caller)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'account/assign_role.html')
        self.assertContains(response, '&lt;script&gt;unsafe&lt;/script&gt;')
        self.assertNotContains(response, '<script>unsafe</script>')
        self.assertContains(response, f'data-user-id="{self.target.pk}"')
        self.assertContains(response, f'value="{self.old.pk}" selected')
        self.assertContains(response, f'value="{self.new.pk}"')
        self.assertNotContains(response, ' multiple')
        self.assertContains(response, f'action="{reverse("account:assign-group")}"')
        self.assertContains(response, f'data-success-url="{reverse("account_pages:user-list")}"')
        self.assertContains(response, 'account/js/assign_role.js')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response.context['display_name'], self.caller.username)

    def test_page_csrf_assigns_one_role_then_user_list_loads(self):
        self.client.force_login(self.caller)
        self.client.get(self.url)
        token = self.client.cookies[settings.CSRF_COOKIE_NAME].value
        response = self.client.post(
            reverse('account:assign-group'), {'user': self.target.pk, 'group': self.new.pk},
            format='json', HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(list(self.target.groups.all()), [self.new])
        self.assertFalse(self.caller.groups.exists())
        self.assertEqual(self.client.get(reverse('account_pages:user-list')).status_code, 200)

    def test_empty_role_catalog_disables_save_and_links_to_add_role(self):
        Group.objects.all().delete()
        self.client.force_login(self.caller)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No roles are available.')
        self.assertContains(response, 'Current role: Not assigned')
        self.assertContains(response, 'data-assignment-save disabled')
        self.assertContains(response, f'href="{reverse("account_pages:add-role")}"')
