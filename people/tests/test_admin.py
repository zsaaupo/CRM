from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from people.models import Party


class PartyAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = get_user_model().objects.create_superuser(username='party-history-admin', password=None)
        cls.live = Party.objects.create(code='LIVE', name='Current supplier', is_supplier=True)
        cls.deleted = Party.objects.create(code='DELETED', name='Historical customer', is_customer=True,
                                           create_by='original', modified_by='original')
        cls.deleted.delete(actor='original-deleter')

    def setUp(self):
        self.client.force_login(self.admin_user)
        self.list_url = reverse('admin:people_party_changelist')
        self.deleted_url = reverse('admin:people_party_change', args=[self.deleted.pk])

    def test_admin_lists_searches_and_filters_live_and_deleted_parties(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual({obj.pk for obj in response.context['cl'].result_list}, {self.live.pk, self.deleted.pk})
        self.assertContains(response, 'original-deleter')
        for value, expected in [('1', self.deleted.pk), ('0', self.live.pk)]:
            response = self.client.get(self.list_url, {'is_deleted__exact': value})
            self.assertEqual([obj.pk for obj in response.context['cl'].result_list], [expected])
        response = self.client.get(self.list_url, {'q': 'Historical'})
        self.assertEqual([obj.pk for obj in response.context['cl'].result_list], [self.deleted.pk])

    def test_deleted_detail_is_read_only_even_for_superusers(self):
        response = self.client.get(self.deleted_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Historical customer')
        self.assertContains(response, 'original-deleter')
        self.assertFalse(response.context['has_change_permission'])
        self.assertFalse(response.context['has_delete_permission'])
        self.assertNotContains(response, 'name="code"')
        self.assertNotContains(response, 'name="_save"')
        self.assertNotContains(response, 'class="deletelink"')
        self.assertEqual(self.client.post(self.deleted_url, {
            'code': 'RESTORED', 'name': 'Changed', 'is_customer': 'on', 'is_deleted': '', '_save': 'Save',
        }).status_code, 403)
        self.assertEqual(self.client.post(reverse('admin:people_party_delete', args=[self.deleted.pk]),
                                          {'post': 'yes'}).status_code, 403)
        self.deleted.refresh_from_db()
        self.assertTrue(self.deleted.is_deleted)
        self.assertEqual(self.deleted.name, 'Historical customer')
        self.assertEqual(self.deleted.deleted_by, 'original-deleter')

    def test_live_parties_remain_editable_and_bulk_deletion_preserves_history(self):
        live_url = reverse('admin:people_party_change', args=[self.live.pk])
        page = self.client.get(live_url)
        self.assertTrue(page.context['has_change_permission'])
        self.assertTrue(page.context['has_delete_permission'])
        response = self.client.post(live_url, {'code': 'LIVE', 'name': 'Updated supplier',
            'is_supplier': 'on', 'is_active': 'on', '_save': 'Save'})
        self.assertEqual(response.status_code, 302)
        self.live.refresh_from_db()
        self.assertEqual(self.live.name, 'Updated supplier')
        deleted_at = self.deleted.deleted_at
        response = self.client.post(self.list_url, {
            'action': 'delete_selected', '_selected_action': [str(self.live.pk), str(self.deleted.pk)], 'post': 'yes',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Party.all_objects.count(), 2)
        self.assertEqual(Party.objects.count(), 0)
        self.deleted.refresh_from_db()
        self.assertEqual(self.deleted.deleted_at, deleted_at)
        self.assertEqual(self.deleted.deleted_by, 'original-deleter')
        self.assertEqual(Party.all_objects.get(pk=self.live.pk).deleted_by, self.admin_user.username)

    def test_staff_need_party_view_or_change_permission_to_read_deleted_records(self):
        staff = get_user_model().objects.create_user(username='party-history-reader', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.list_url).status_code, 403)
        self.assertEqual(self.client.get(self.deleted_url).status_code, 403)
        staff.user_permissions.add(Permission.objects.get(content_type__app_label='people', codename='view_party'))
        self.assertEqual(self.client.get(self.list_url).status_code, 200)
        response = self.client.get(self.deleted_url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['has_change_permission'])
        self.assertEqual(self.client.get(reverse('people:party-detail', args=[self.deleted.pk])).status_code, 404)
