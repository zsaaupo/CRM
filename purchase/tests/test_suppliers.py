from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from people.models import Party
from purchase.models import PurchaseOrder


class PurchaseSupplierTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='purchase-supplier-operator')
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label='purchase',
            codename__in=['add_purchaseorder', 'change_purchaseorder', 'view_purchaseorder', 'add_purchaseorderitem']))
        self.supplier = Party.objects.create(code='SUP', name='Supplier company', is_supplier=True)
        self.both = Party.objects.create(code='BOTH', name='Both roles', is_supplier=True, is_customer=True)
        self.inactive = Party.objects.create(code='INACTIVE', name='Inactive supplier', is_supplier=True, is_active=False)
        self.customer = Party.objects.create(code='CUSTOMER', name='Customer only', is_customer=True)
        self.deleted = Party.objects.create(code='DELETED', name='Deleted supplier', is_supplier=True)
        self.deleted.delete(actor='original')
        self.order = PurchaseOrder.objects.create(currency_id='BDT', exchange_rate='1', supplier=self.supplier)
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)
        self.url = reverse('purchase:purchase-order-list')
        self.detail = reverse('purchase:purchase-order-detail', args=[self.order.pk])
        self.update = reverse('purchase:purchase-order-update', args=[self.order.pk])

    def payload(self, supplier):
        return {'currency': 'BDT', 'exchange_rate': '1', 'supplier': supplier}

    def test_dropdown_only_lists_non_deleted_suppliers_without_party_view_permission(self):
        response = self.client.get(reverse('purchase:purchase-options'))
        self.assertEqual(response.status_code, 200)
        rows = response.data['supplier']
        self.assertEqual({row['value'] for row in rows}, {self.supplier.pk, self.both.pk, self.inactive.pk})
        self.assertIn({'value': self.supplier.pk, 'label': 'Supplier company (SUP)'}, rows)
        self.assertEqual(self.client.get(reverse('people:party-list')).status_code, 403)
        page = self.client.get(reverse('purchase_pages:create-purchase'))
        self.assertContains(page, '<select id="id_supplier" name="supplier"')
        self.assertContains(page, 'Only parties marked as suppliers are listed.')

    def test_new_and_updated_suppliers_are_validated_on_every_write_endpoint(self):
        for invalid in [self.customer.pk, self.deleted.pk, 999999, 'Supplier company']:
            with self.subTest(supplier=invalid):
                response = self.client.post(self.url, self.payload(invalid), format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn('supplier', response.data)
                for url in [self.detail, self.update]:
                    self.assertEqual(self.client.patch(url, {'supplier': invalid}, format='json').status_code, 400)
                    self.assertEqual(self.client.put(url, self.payload(invalid), format='json').status_code, 400)
        self.order.refresh_from_db()
        self.assertEqual(self.order.supplier_id, self.supplier.pk)
        for supplier in [self.supplier, self.both, self.inactive]:
            response = self.client.post(self.url, {**self.payload(supplier.pk),
                'supplier_name': 'Spoof', 'supplier_code': 'Spoof'}, format='json')
            self.assertEqual(response.status_code, 201, response.data)
            self.assertEqual(response.data['purchase_order']['supplier'], supplier.pk)
            self.assertEqual(response.data['purchase_order']['supplier_name'], supplier.name)
            self.assertEqual(response.data['purchase_order']['supplier_code'], supplier.code)
        self.assertEqual(self.client.patch(self.update, {'supplier': self.both.pk}, format='json').status_code, 200)
        self.assertEqual(self.client.patch(self.update, {'supplier': None}, format='json').status_code, 200)

    def test_model_validation_rejects_ineligible_new_assignments(self):
        for supplier in [self.customer, self.deleted]:
            with self.subTest(supplier=supplier.code):
                candidate = PurchaseOrder(currency_id='BDT', exchange_rate='1', supplier=supplier)
                with self.assertRaises(ValidationError):
                    candidate.full_clean(exclude=['create_by', 'modified_by'])
        self.order.supplier = self.customer
        with self.assertRaises(ValidationError):
            self.order.full_clean(exclude=['create_by', 'modified_by'])

    def test_soft_deleted_supplier_remains_readable_on_history_but_cannot_be_selected_again(self):
        self.supplier.delete(actor='party-admin')
        response = self.client.get(self.detail)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['supplier_name'], 'Supplier company')
        self.assertEqual(response.data['supplier'], self.supplier.pk)
        self.assertEqual(self.client.patch(self.update, {'notes': 'Updated history'}, format='json').status_code, 200)
        self.assertEqual(self.client.patch(self.update, {'supplier': self.supplier.pk}, format='json').status_code, 400)
        self.assertEqual(self.client.post(self.url, self.payload(self.supplier.pk), format='json').status_code, 400)
        data = self.client.get(reverse('purchase:purchase-order-list-view'), {'q': 'Supplier company'}).data
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['supplier_name'], 'Supplier company')
        candidate = PurchaseOrder.objects.get(pk=self.order.pk)
        candidate.full_clean(exclude=['create_by', 'modified_by'])

    def test_supplier_role_removal_preserves_existing_orders_and_prevents_new_assignments(self):
        self.supplier.is_supplier = False
        self.supplier.is_customer = True
        self.supplier.save()
        self.assertEqual(self.client.get(self.detail).data['supplier_name'], self.supplier.name)
        self.assertEqual(self.client.patch(self.update, {'notes': 'Existing order'}, format='json').status_code, 200)
        self.assertEqual(self.client.post(self.url, self.payload(self.supplier.pk), format='json').status_code, 400)

    def test_admin_can_soft_delete_a_supplier_referenced_by_a_purchase(self):
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label='people',
                                        codename__in=['view_party', 'delete_party']))
        delete_url = reverse('admin:people_party_delete', args=[self.supplier.pk])
        response = self.client.get(delete_url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['protected'])
        self.assertEqual(self.client.post(delete_url, {'post': 'yes'}).status_code, 302)
        self.assertTrue(Party.all_objects.get(pk=self.supplier.pk).is_deleted)
        self.assertEqual(PurchaseOrder.objects.get(pk=self.order.pk).supplier_id, self.supplier.pk)
        self.assertEqual(self.client.get(self.detail).data['supplier_name'], 'Supplier company')
