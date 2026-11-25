from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from common.io_utils import build_xlsx_bytes, bulk_delete_queryset, read_xlsx_rows
from common.models import ImportJob
from inventory.models import Brand, Currency, Product, ProductType, UnitOfMeasure
from people.models import Biller, Party
from warehouse.models import RoleWarehouseAccess, Stock, WareHouse


class Ops08Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(username='ops08-admin', password='pw')
        cls.wh_a = WareHouse.objects.create(name='WH Alpha', address='Dhaka', phone='01')
        cls.wh_b = WareHouse.objects.create(name='WH Beta', address='Chittagong', phone='02')
        cls.unit = UnitOfMeasure.objects.create(name='Pieces', code='OPS-PCS')
        cls.product = Product.objects.create(
            sku='OPS-SKU-1', name='Widget A', unit=cls.unit,
            currency_id='BDT', cost_price=10, sale_price=15,
        )
        Stock.objects.create(warehouse=cls.wh_a, product=cls.product, quantity=5)
        Stock.objects.create(warehouse=cls.wh_b, product=cls.product, quantity=9)
        cls.party = Party.objects.create(name='Customer One', code='OPS-C1', is_customer=True)

        # Limited role restricted to wh_a
        cls.limited_role = Group.objects.create(name='wh_a_staff')
        RoleWarehouseAccess.objects.create(role=cls.limited_role, warehouse=cls.wh_a, all_warehouses=False)
        perms = Permission.objects.filter(
            codename__in=['view_stock', 'manage_biller', 'view_biller', 'add_biller'],
        )
        cls.limited_role.permissions.add(*perms)
        cls.limited_user = get_user_model().objects.create_user(username='ops08-wh-a', password='pw')
        cls.limited_user.groups.add(cls.limited_role)

    def test_template_dry_run_malformed_row_and_idempotent_reimport(self):
        client = APIClient()
        client.force_login(self.admin)

        # 1. Template download
        tmpl = client.get(reverse('common:import-template', args=['brands']))
        self.assertEqual(tmpl.status_code, 200)
        self.assertIn(b'external_key,name', tmpl.content)

        # 2. Malformed row (blank name + duplicate external_key) rejects entire batch
        bad_csv = 'external_key,name,website,description\nk1,Brand One,,\nk2,,,missing name\n'
        prev_bad = client.post(reverse('common:import-preview', args=['brands']), {'csv': bad_csv}, format='json')
        self.assertEqual(prev_bad.status_code, 200)
        self.assertFalse(prev_bad.data['dry_run_result']['valid'])
        self.assertEqual(prev_bad.data['status'], ImportJob.Status.FAILED)

        commit_bad = client.post(
            reverse('common:import-commit', args=[prev_bad.data['id']]),
            {'request_key': str(uuid4())}, format='json',
        )
        self.assertEqual(commit_bad.status_code, 400)
        self.assertFalse(Brand.objects.filter(name='Brand One').exists())

        # 3. Valid CSV dry-run + commit
        good_csv = 'external_key,name,website,description\nb-k1,Brand Alpha,,\nb-k2,Brand Beta,,\n'
        prev_ok = client.post(reverse('common:import-preview', args=['brands']), {'csv': good_csv}, format='json')
        self.assertEqual(prev_ok.status_code, 200)
        self.assertTrue(prev_ok.data['dry_run_result']['valid'])
        key = str(uuid4())
        commit1 = client.post(reverse('common:import-commit', args=[prev_ok.data['id']]), {'request_key': key}, format='json')
        self.assertEqual(commit1.status_code, 200, commit1.data)
        self.assertEqual(commit1.data['committed_result']['committed_count'], 2)
        self.assertEqual(Brand.objects.filter(name__startswith='Brand ').count(), 2)

        # 4. Same request_key replay -> no double commit
        replay = client.post(reverse('common:import-commit', args=[prev_ok.data['id']]), {'request_key': key}, format='json')
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.data['idempotent_replay'])

        # 5. Re-importing same file via NEW ImportJob + NEW request_key produces zero extra records
        prev2 = client.post(reverse('common:import-preview', args=['brands']), {'csv': good_csv}, format='json')
        self.assertEqual(len(prev2.data['dry_run_result']['duplicates']), 2)
        self.assertEqual(prev2.data['dry_run_result']['new_rows'], 0)
        commit2 = client.post(
            reverse('common:import-commit', args=[prev2.data['id']]),
            {'request_key': str(uuid4())}, format='json',
        )
        self.assertEqual(commit2.status_code, 200)
        self.assertEqual(commit2.data['committed_result']['committed_count'], 0)
        self.assertEqual(Brand.objects.filter(name__startswith='Brand ').count(), 2)

    def test_scope_limited_import_and_export_and_formats(self):
        # Scope-limited user cannot import a biller mapped to wh_b
        limited_client = APIClient()
        limited_client.force_login(self.limited_user)
        csv_out = f'external_key,name,warehouse\nbil-out,Beta Desk,{self.wh_b.pk}\n'
        prev = limited_client.post(reverse('common:import-preview', args=['billers']), {'csv': csv_out}, format='json')
        self.assertEqual(prev.status_code, 200)
        self.assertFalse(prev.data['dry_run_result']['valid'])
        self.assertIn('outside your access scope', prev.data['dry_run_result']['errors'][0]['error'])
        self.assertFalse(Biller.objects.filter(name='Beta Desk').exists())

        # Scoped Stock export only contains wh_a
        stock_csv = limited_client.get(reverse('warehouse:stock-list'), {'export': 'csv'})
        self.assertEqual(stock_csv.status_code, 200)
        body = stock_csv.content.decode('utf-8-sig')
        self.assertIn('WH Alpha', body)
        self.assertNotIn('WH Beta', body)

        # XLSX, PDF, Print exports on admin
        admin_client = APIClient()
        admin_client.force_login(self.admin)
        prod_xlsx = admin_client.get(reverse('inventory:product-list'), {'export': 'xlsx'})
        self.assertEqual(prod_xlsx.status_code, 200)
        rows = read_xlsx_rows(prod_xlsx.content)
        self.assertEqual(rows[0][0], 'SKU')
        self.assertIn('OPS-SKU-1', [r[0] for r in rows[1:]])

        party_pdf = admin_client.get(reverse('people:party-list'), {'export': 'pdf'})
        self.assertEqual(party_pdf.status_code, 200)
        self.assertTrue(party_pdf.content.startswith(b'%PDF'))

        party_print = admin_client.get(reverse('people:party-list'), {'export': 'print'})
        self.assertEqual(party_print.status_code, 200)
        self.assertIn(b'Customer One', party_print.content)

    def test_xlsx_import_and_bulk_soft_delete(self):
        xlsx = build_xlsx_bytes(['external_key', 'name', 'code'], [['u-k1', 'Dozen', 'OPS-DZN']])
        client = APIClient()
        client.force_login(self.admin)
        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile('units.xlsx', xlsx, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        prev = client.post(reverse('common:import-preview', args=['units']), {'file': f})
        self.assertEqual(prev.status_code, 200, prev.data)
        self.assertTrue(prev.data['dry_run_result']['valid'])
        res = client.post(reverse('common:import-commit', args=[prev.data['id']]), {'request_key': str(uuid4())}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(UnitOfMeasure.objects.filter(code='OPS-DZN').exists())

        # Bulk delete soft-deletes when is_active exists
        del_res = bulk_delete_queryset(
            self.admin, UnitOfMeasure.objects.filter(code='OPS-DZN'),
            permission_codename='inventory.delete_unitofmeasure',
        )
        self.assertEqual(del_res, {'deleted_count': 1, 'soft_deleted': True})
        self.assertFalse(UnitOfMeasure.objects.get(code='OPS-DZN').is_active)
