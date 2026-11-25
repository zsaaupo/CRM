from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from people.models import Biller, BillerWarehouse, CustomerGroup, Party
from warehouse.models import WareHouse
from warehouse.tests.helpers import assign_warehouse_role


class People01aTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(username='p01a-admin')
        cls.cashier = get_user_model().objects.create_user(username='p01a-cashier')
        cls.wh1 = WareHouse.objects.create(name='Branch 1', address='Dhaka', phone='017')
        cls.wh2 = WareHouse.objects.create(name='Branch 2', address='Sylhet', phone='018')
        cls.role = assign_warehouse_role(cls.cashier, warehouse=cls.wh1).role
        cls.role.permissions.add(
            Permission.objects.get(content_type__app_label='people', codename='view_biller'),
            Permission.objects.get(content_type__app_label='people', codename='view_customergroup'),
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_login(self.admin)

    def test_biller_crud_and_warehouse_scoped_options(self):
        b1 = self.client.post(reverse('people:biller-list'), {
            'name': 'Biller One', 'company_name': 'Co 1', 'warehouses': [self.wh1.pk],
        }, format='json')
        self.assertEqual(b1.status_code, 201, b1.data)
        b2 = self.client.post(reverse('people:biller-list'), {
            'name': 'Biller Two', 'company_name': 'Co 2', 'warehouses': [self.wh2.pk],
        }, format='json')
        self.assertEqual(b2.status_code, 201, b2.data)

        # Cashier scoped to wh1 sees only Biller One in options
        self.client.force_login(self.cashier)
        opt = self.client.get(reverse('people:biller-options'))
        self.assertEqual(opt.status_code, 200)
        self.assertEqual([row['label'] for row in opt.data['results']], ['Biller One'])

        # Asking explicitly for wh2 returns empty for wh1-scoped cashier
        opt_wh2 = self.client.get(reverse('people:biller-options'), {'warehouse_id': self.wh2.pk})
        self.assertEqual(opt_wh2.data['results'], [])

        # Cashier without manage_biller cannot create biller
        denied = self.client.post(reverse('people:biller-list'), {'name': 'Nope'}, format='json')
        self.assertEqual(denied.status_code, 403)

    def test_customer_group_crud_and_party_link(self):
        grp = self.client.post(reverse('people:customer-group-list'), {
            'name': 'Wholesale VIP', 'percentage': '10.00', 'price_basis': 'wholesale',
        }, format='json')
        self.assertEqual(grp.status_code, 201, grp.data)
        grp_id = grp.data['customer_group']['id']

        party = self.client.post(reverse('people:party-list'), {
            'code': 'VIP-01', 'name': 'VIP Client', 'is_customer': True, 'customer_group': grp_id,
        }, format='json')
        self.assertEqual(party.status_code, 201, party.data)
        self.assertEqual(Party.objects.get(pk=party.data['party']['id']).customer_group_id, grp_id)
