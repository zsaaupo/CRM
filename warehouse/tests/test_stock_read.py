from warehouse.tests.helpers import assign_warehouse_role
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounting.models import FinancialAccount
from inventory.models import Currency, Product, ProductCategory, UnitOfMeasure
from purchase.models import PurchaseOrder, PurchaseOrderItem
from warehouse.api_views import StockListAPIView
from warehouse.models import Stock, WareHouse
from warehouse.serializers import StockReadSerializer


class StockReadTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='stock-reader')
        self.role = assign_warehouse_role(self.user, all_warehouses=True).role
        self.warehouse = WareHouse.objects.create(name='Main', code='MAIN', address='Dhaka', phone='1')
        self.branch = WareHouse.objects.create(name='Branch', address='Dhaka', phone='2')
        self.unit = UnitOfMeasure.objects.create(name='Kilograms', code='READ-KG')
        self.product = Product.objects.create(name='Rice', sku='READ-RICE', unit=self.unit, currency_id='BDT')
        self.stock = Stock.objects.create(warehouse=self.warehouse, product=self.product, quantity='12.123456',
                                          create_by='original', modified_by='original')
        self.client = APIClient()
        self.client.force_login(self.user)
        self.list_api = reverse('warehouse:stock-list')
        self.chart_api = reverse('warehouse:stock-chart')
        self.detail_api = reverse('warehouse:stock-detail', args=[self.stock.pk])
        self.list_page = reverse('warehouse_pages:stock-list')
        self.chart_page = reverse('warehouse_pages:stock-chart')
        self.detail_page = reverse('warehouse_pages:stock-detail', args=[self.stock.pk])

    def grant(self, *permissions):
        for value in permissions:
            app, codename = value.split('.')
            self.role.permissions.add(Permission.objects.get(content_type__app_label=app, codename=codename))

    def test_stock_view_permission_is_required_independently_of_warehouse_access(self):
        for url in [self.list_api, self.chart_api, self.detail_api]:
            self.assertEqual(APIClient().get(url).status_code, 403)
            self.assertEqual(self.client.get(url).status_code, 403)
        for url in [self.list_page, self.chart_page, self.detail_page]:
            self.assertRedirects(Client().get(url), reverse('account_pages:login'))
            self.assertEqual(self.client.get(url).status_code, 403)
        self.grant('warehouse.view_warehouse')
        self.assertNotContains(self.client.get(reverse('dashboard')), '>Stock list</a>')
        self.assertEqual(self.client.get(self.list_api).status_code, 403)
        self.grant('warehouse.view_stock')
        for url in [self.list_api, self.chart_api, self.detail_api, self.list_page, self.chart_page, self.detail_page]:
            self.assertEqual(self.client.get(url).status_code, 200)

    def test_chart_values_currency_conversion_branch_filter_and_missing_rates(self):
        self.grant('warehouse.view_stock')
        category = ProductCategory.objects.create(name='Devices', code='DEVICES')
        self.product.category = category
        self.product.cost_price = '10'
        self.product.sale_price = '15'
        self.product.save(update_fields=['category', 'cost_price', 'sale_price'])
        self.stock.quantity = '2'
        self.stock.save(update_fields=['quantity'])

        drinks = ProductCategory.objects.create(name='Drinks', code='DRINKS')
        usd = Currency.objects.create(code='USD', name='US Dollar', exchange_rate='120')
        foreign_product = Product.objects.create(
            name='Tea', sku='READ-TEA', unit=self.unit, currency=usd,
            cost_price='2', sale_price='3', category=drinks,
        )
        Stock.objects.create(warehouse=self.branch, product=foreign_product, quantity='4')
        missing_rate = Currency.objects.create(code='EUR', name='Euro', exchange_rate=None)
        unpriced = Product.objects.create(
            name='Coffee', sku='READ-COFFEE', unit=self.unit, currency=missing_rate,
            cost_price='5', sale_price='8',
        )
        Stock.objects.create(warehouse=self.branch, product=unpriced, quantity='1')

        response = self.client.get(self.chart_api)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['currency'], 'BDT')
        self.assertEqual(response.data['warehouse_label'], 'All branches')
        self.assertEqual(response.data['totals'], {
            'stock_value_by_price': '1470.0000',
            'stock_value_by_cost': '980.0000',
            'estimated_profit': '490.0000',
        })
        self.assertEqual(response.data['total_items'], 3)
        self.assertEqual(response.data['total_quantity'], '7.000000')
        self.assertEqual(response.data['quantity_unit_count'], 1)
        self.assertEqual(response.data['missing_exchange_rate_currencies'], ['EUR'])
        selected = self.client.get(self.chart_api, {'warehouse': self.branch.pk})
        self.assertEqual(selected.data['warehouse']['id'], self.branch.pk)
        self.assertEqual(selected.data['totals']['stock_value_by_price'], '1440.0000')
        self.assertEqual(self.client.get(self.chart_api, {'warehouse': 'bad'}).status_code, 400)
        self.assertEqual(self.client.get(self.chart_api, {'warehouse': 999999}).status_code, 404)

        assignment = self.user.groups.get().warehouse_access
        assignment.warehouse = self.warehouse
        assignment.all_warehouses = False
        assignment.save()
        scoped = self.client.get(self.chart_api)
        self.assertEqual(scoped.data['warehouse']['id'], self.warehouse.pk)
        self.assertEqual(scoped.data['totals']['stock_value_by_price'], '30.0000')
        self.assertEqual(self.client.get(self.chart_api, {'warehouse': self.branch.pk}).status_code, 404)
        scoped_page = self.client.get(self.chart_page)
        self.assertFalse(scoped_page.context['choose_warehouse'])
        self.assertNotContains(scoped_page, 'id="stock-chart-warehouse"')

    def test_read_apis_return_stock_identifiers_labels_and_exact_quantities(self):
        self.grant('warehouse.view_stock')
        response = self.client.get(self.list_api)
        record = response.data['results'][0]
        self.assertEqual(record['id'], self.stock.pk)
        self.assertEqual(record['warehouse'], self.warehouse.pk)
        self.assertEqual(record['warehouse_name'], 'Main')
        self.assertEqual(record['warehouse_code'], 'MAIN')
        self.assertEqual(record['product'], self.product.pk)
        self.assertEqual(record['product_name'], 'Rice')
        self.assertEqual(record['product_sku'], 'READ-RICE')
        self.assertEqual(record['unit_name'], 'Kilograms')
        self.assertEqual(record['unit_code'], 'READ-KG')
        self.assertEqual(record['quantity'], '12.123456')
        self.assertEqual(self.client.get(self.detail_api).data, record)
        self.assertEqual(self.client.get(reverse('warehouse:stock-detail', args=[99999])).status_code, 404)
        self.assertEqual(self.client.get(reverse('inventory:product-list-view')).status_code, 403)

    def test_stock_read_endpoints_allow_only_get_head_and_options_even_with_write_grants(self):
        self.grant('warehouse.view_stock', 'warehouse.add_stock', 'warehouse.change_stock', 'warehouse.delete_stock')
        for url in [self.list_api, self.chart_api, self.detail_api]:
            self.assertIn('no-store', self.client.get(url)['Cache-Control'])
            self.assertEqual(self.client.head(url).status_code, 200)
            self.assertEqual(self.client.options(url).status_code, 200)
            for method in ['post', 'put', 'patch', 'delete']:
                self.assertEqual(getattr(self.client, method)(url, {}, format='json').status_code, 405)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('12.123456'))

    def test_search_filters_and_twenty_row_pagination(self):
        self.grant('warehouse.view_stock')
        products = Product.objects.bulk_create([Product(name=f'Bulk product {i:02}', sku=f'BULK-READ-{i}',
            unit=self.unit, currency_id='BDT') for i in range(25)])
        Stock.objects.bulk_create([Stock(warehouse=self.warehouse, product=product, quantity=1) for product in products])
        data = self.client.get(self.list_api, {'q': 'Bulk product'}).data
        self.assertEqual(data['count'], 25)
        self.assertEqual(len(data['results']), 20)
        self.assertEqual(data['results'][0]['product_name'], 'Bulk product 00')
        self.assertEqual(len(self.client.get(data['next']).data['results']), 5)
        self.assertEqual(self.client.get(self.list_api, {'q': 'READ-RICE'}).data['count'], 1)
        self.assertEqual(self.client.get(self.list_api, {'warehouse': self.branch.pk}).data['count'], 0)
        self.assertEqual(self.client.get(self.list_api, {'product': self.product.pk}).data['count'], 1)
        for params in [{'warehouse': 'bad'}, {'product': 0}, {'product': 10 ** 30}]:
            self.assertEqual(self.client.get(self.list_api, params).status_code, 400)

    def test_pages_use_existing_design_and_have_only_view_actions_even_for_superusers(self):
        self.user.is_staff = self.user.is_superuser = True
        self.user.save()
        page = self.client.get(self.list_page)
        self.assertContains(page, f'data-api-url="{self.list_api}"')
        self.assertContains(page, 'data-view-url-template="/warehouse/stocks/0/"')
        self.assertContains(page, '/static/inventory/js/catalog_list.js')
        self.assertContains(page, 'aria-current="page">Stock list')
        self.assertNotContains(page, 'aria-current="page">Warehouse list')
        self.assertContains(page, 'users-search')
        self.assertContains(page, 'data-catalog-pagination')
        self.assertNotContains(page, 'data-catalog-delete-dialog')
        self.assertNotContains(page, 'data-catalog-receive-form')
        chart_page = self.client.get(self.chart_page)
        self.assertContains(chart_page, f'data-api-url="{self.chart_api}"')
        self.assertContains(chart_page, '/static/warehouse/js/stock_chart.js')
        self.assertContains(chart_page, 'id="stock-chart-warehouse"')
        self.assertContains(chart_page, 'data-total-items')
        self.assertContains(chart_page, 'data-total-quantity')
        self.assertContains(chart_page, 'data-stock-chart-svg width="320" height="320"')
        self.assertNotContains(chart_page, 'stock-chart-metric')
        self.assertContains(chart_page, 'aria-current="page">Stock chart')
        self.assertContains(chart_page, 'FIFO or weighted-average purchase valuations')
        self.assertTrue(chart_page.context['choose_warehouse'])
        for key in ['can_add', 'can_delete', 'edit_url_template', 'delete_url_template', 'stock_url_template', 'receive_api_url']:
            self.assertNotIn(key, page.context)
        self.assertEqual([column['key'] for column in page.context['columns']],
                         ['warehouse_name', 'product_sku', 'product_name', 'quantity', 'unit_code', 'actions'])
        detail = self.client.get(self.detail_page)
        self.assertContains(detail, f'data-api-url="{self.detail_api}"')
        self.assertContains(detail, f'href="{self.list_page}"')
        self.assertContains(detail, '/static/warehouse/js/stock_detail.js')
        self.assertContains(detail, 'data-detail-field="quantity"')
        self.assertContains(detail, 'data-record-retry')
        self.assertNotIn('form', detail.context)
        self.assertIn('no-store', detail['Cache-Control'])

    def test_stock_only_role_sees_stock_navigation_and_revocation_removes_access(self):
        role = Group.objects.create(name='Stock viewers')
        role.permissions.add(Permission.objects.get(content_type__app_label='warehouse', codename='view_stock'))
        assign_warehouse_role(self.user, all_warehouses=True, role=role)
        dashboard = self.client.get(reverse('dashboard'))
        self.assertContains(dashboard, 'data-warehouses-toggle')
        self.assertContains(dashboard, f'href="{self.list_page}"')
        self.assertContains(dashboard, f'href="{self.chart_page}"')
        self.assertNotContains(dashboard, '>Warehouse list</a>')
        self.assertNotContains(dashboard, '>Add warehouse</a>')
        self.assertEqual(self.client.get(self.list_page).status_code, 200)
        role.permissions.clear()
        self.assertEqual(self.client.get(self.list_api).status_code, 403)
        self.assertEqual(self.client.get(self.detail_page).status_code, 403)
        self.assertNotContains(self.client.get(reverse('dashboard')), '>Stock list</a>')
        self.assertNotContains(self.client.get(reverse('dashboard')), '>Stock chart</a>')

    def test_receiving_a_purchase_updates_the_list_and_details_without_duplicate_stock_rows(self):
        self.grant('warehouse.view_stock', 'purchase.change_purchaseorder')
        account = FinancialAccount.objects.create(name='Purchase bank', type='bank', warehouse=self.warehouse,
                                                  opening_balance=100)
        order = PurchaseOrder.objects.create(currency_id='BDT', exchange_rate=1, warehouse=self.warehouse,
                                              payment_account=account)
        PurchaseOrderItem.objects.create(purchase_order=order, line_number=1, product=self.product, unit=self.unit,
                                         quantity='2', unit_price=1)
        response = self.client.post(reverse('purchase:purchase-order-receive'),
                                    {'purchase_id': order.pk, 'is_received': True}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        record = self.client.get(self.list_api).data['results'][0]
        self.assertEqual(record['id'], self.stock.pk)
        self.assertEqual(record['quantity'], '14.123456')
        self.assertEqual(self.client.get(self.detail_api).data['quantity'], '14.123456')
        self.assertEqual(Stock.objects.count(), 1)

    def test_labels_are_loaded_without_queries_per_stock_record(self):
        Stock.objects.create(warehouse=self.branch, product=self.product, quantity=2)
        with self.assertNumQueries(1):
            data = StockReadSerializer(StockListAPIView.queryset.all(), many=True).data
        self.assertEqual(len(data), 2)
