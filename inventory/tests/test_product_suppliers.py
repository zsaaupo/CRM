from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from inventory.models import Product, UnitOfMeasure
from people.models import Party


class ProductSupplierTests(TestCase):
    def test_product_supplier_choices_assignment_and_historical_link(self):
        unit = UnitOfMeasure.objects.create(name='Pieces', code='SUPPLIER-PCS')
        supplier = Party.objects.create(name='Supplier', code='PRD-S1', is_supplier=True)
        customer = Party.objects.create(name='Customer', code='PRD-C1', is_customer=True)
        deleted = Party.objects.create(name='Deleted supplier', code='PRD-S2', is_supplier=True)
        deleted.delete()
        actor = get_user_model().objects.create_superuser(username='catalog-suppliers', email='catalog@example.com')
        client = APIClient()
        client.force_login(actor)

        options = client.get(reverse('inventory:product-options')).data['supplier']
        self.assertEqual(options, [{'value': supplier.pk, 'label': 'Supplier (PRD-S1)'}])
        payload = {'sku': 'PRD-1', 'name': 'Product', 'unit': unit.pk, 'currency': 'BDT'}
        create = reverse('inventory:product-list')
        for ineligible in [customer.pk, deleted.pk]:
            response = client.post(create, {**payload, 'supplier': ineligible}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn('supplier', response.data)

        response = client.post(create, {**payload, 'supplier': str(supplier.pk)}, format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        product = Product.objects.get(sku='PRD-1')
        detail = reverse('inventory:product-detail', args=[product.pk])
        self.assertEqual(client.get(detail).data['supplier_name'], supplier.name)

        supplier.delete()
        edit_options = client.get(reverse('inventory:product-edit-options', args=[product.pk])).data['supplier']
        self.assertEqual(edit_options, [{'value': supplier.pk, 'label': 'Supplier (PRD-S1) (unavailable)'}])
        update = reverse('inventory:product-update', args=[product.pk])
        response = client.patch(update, {'name': 'Updated product', 'supplier': supplier.pk}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['supplier'], supplier.pk)
        response = client.patch(update, {'supplier': None}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data['supplier'])
