from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client, TestCase
from django.urls import reverse

from inventory.models import Product, UnitOfMeasure
from purchase.models import PurchaseOrder, PurchaseOrderItem
from purchase.serializers import PurchaseOrderDetailSerializer


class PurchaseDetailPageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='purchase-reader')
        self.client.force_login(self.user)
        unit = UnitOfMeasure.objects.create(name='Pieces', code='PCS')
        self.product = Product.objects.create(name='Keyboard', sku='KEY', unit=unit, currency_id='BDT')
        self.order = PurchaseOrder.objects.create(currency_id='BDT', exchange_rate='1', supplier=None, shipping_cost='5')
        self.item = PurchaseOrderItem.objects.create(purchase_order=self.order, product=self.product, unit=unit,
                                                    line_number=1, quantity='3', unit_price='10', tax_rate='15')
        self.url = reverse('purchase_pages:purchase-detail', args=[self.order.pk])
        self.api_url = reverse('purchase:purchase-order-detail', args=[self.order.pk])

    def grant(self, *codenames):
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label='purchase', codename__in=codenames))

    def test_read_only_detail_page_and_list_action_use_order_permission(self):
        self.assertRedirects(Client().get(self.url), reverse('account_pages:login'))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(self.api_url).status_code, 403)
        self.grant('view_purchaseorder')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertContains(response, f'data-api-url="{self.api_url}"')
        self.assertContains(response, '/static/purchase/js/purchase_detail.js')
        self.assertContains(response, 'data-record-content hidden')
        self.assertContains(response, 'aria-current="page">Purchase list')
        self.assertNotContains(response, 'data-purchase-items')
        self.assertNotContains(response, self.order.order_number)
        for name in ['supplier_name', 'order_number', 'order_date', 'exchange_rate', 'subtotal', 'tax_total', 'shipping_cost', 'grand_total']:
            self.assertContains(response, f'data-detail-field="{name}"')
        listing = self.client.get(reverse('purchase_pages:purchase-list'))
        self.assertContains(listing, 'data-view-url-template="/purchase/purchase-orders/0/"')
        self.assertEqual(sum(column['key'] == 'actions' for column in listing.context['columns']), 1)
        data = self.client.get(self.api_url).json()
        self.assertEqual(data['grand_total'], '39.5000')
        self.assertEqual(data['status_name'], 'Draft')
        self.assertTrue(data['currency_name'])
        self.assertIsNone(data['supplier'])
        self.assertNotIn('items', data)

    def test_items_have_separate_permissions_filter_pagination_and_product_links(self):
        self.grant('view_purchaseorder')
        items_url = reverse('purchase:purchase-order-item-list-view')
        self.assertEqual(self.client.get(items_url, {'purchase_order': self.order.pk}).status_code, 403)
        self.grant('view_purchaseorderitem')
        page = self.client.get(self.url)
        self.assertContains(page, f'data-api-url="{items_url}?purchase_order={self.order.pk}"')
        self.assertContains(page, 'data-product-url-template=""')
        data = self.client.get(page.context['items_api_url']).json()
        self.assertEqual(data['count'], 1)
        item = data['results'][0]
        for name, value in {'product_name': 'Keyboard', 'product_sku': 'KEY', 'unit_name': 'Pieces',
                            'unit_code': 'PCS', 'quantity': '3.000000', 'line_total': '34.5000'}.items():
            self.assertEqual(item[name], value)
        other = PurchaseOrder.objects.create(currency_id='BDT', exchange_rate='1')
        PurchaseOrderItem.objects.create(purchase_order=other, product=self.product, unit=self.product.unit,
                                         line_number=1, quantity='1', unit_price='999')
        PurchaseOrderItem.objects.bulk_create([
            PurchaseOrderItem(purchase_order=self.order, product=self.product, unit=self.product.unit,
                              line_number=i, quantity='1', unit_price='1') for i in range(2, 23)
        ])
        first = self.client.get(page.context['items_api_url']).json()
        self.assertEqual(first['count'], 22)
        self.assertEqual(len(first['results']), 20)
        last = self.client.get(first['next']).json()
        self.assertEqual([row['line_number'] for row in last['results']], [21, 22])
        self.assertTrue(all(row['purchase_order'] == self.order.pk for row in first['results'] + last['results']))
        self.user.user_permissions.add(Permission.objects.get(content_type__app_label='inventory', codename='view_product'))
        self.assertContains(self.client.get(self.url), 'data-product-url-template="/inventory/products/0/"')

    def test_queries_missing_record_empty_items_and_revoked_access(self):
        with self.assertNumQueries(2):
            data = PurchaseOrderDetailSerializer(PurchaseOrder.objects.select_related('currency', 'status', 'supplier')
                                                 .prefetch_related('items').get(pk=self.order.pk)).data
        self.assertEqual(data['grand_total'], '39.5000')
        role = Group.objects.create(name='Purchase readers')
        role.permissions.add(*Permission.objects.filter(content_type__app_label='purchase',
                             codename__in=['view_purchaseorder', 'view_purchaseorderitem']))
        self.user.groups.add(role)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        missing = reverse('purchase:purchase-order-detail', args=[999999])
        self.assertEqual(self.client.get(missing).status_code, 404)
        self.assertEqual(self.client.get(reverse('purchase_pages:purchase-detail', args=[999999])).status_code, 200)
        self.item.delete()
        self.assertEqual(self.client.get(reverse('purchase:purchase-order-item-list-view'),
                                         {'purchase_order': self.order.pk}).json()['results'], [])
        role.permissions.clear()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(self.api_url).status_code, 403)
