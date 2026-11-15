from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from common.models import ProductStatus, TradeStatus
from inventory.models import Product, UnitOfMeasure
from purchase.models import PurchaseOrder
from sale.models import SalesOrder


class ProductTradeStatusTests(TestCase):
    def test_trade_status_has_independent_fields_table_and_records(self):
        self.assertFalse(issubclass(TradeStatus, ProductStatus))
        self.assertFalse(TradeStatus._meta.proxy)
        self.assertNotEqual(TradeStatus._meta.db_table, ProductStatus._meta.db_table)
        self.assertIs(TradeStatus._meta.concrete_model, TradeStatus)
        status = ProductStatus.objects.create(code='custom-ready', name='Custom ready')
        self.assertFalse(TradeStatus.objects.filter(code=status.code).exists())
        trade = TradeStatus.objects.create(code=status.code, name='Trade ready')
        trade.name = 'Updated trade label'
        trade.save()
        status.refresh_from_db()
        self.assertEqual(status.name, 'Custom ready')
        trade.delete()
        self.assertTrue(ProductStatus.objects.filter(pk=status.pk).exists())

    def test_products_and_orders_resolve_the_declared_status_classes(self):
        self.assertIs(Product._meta.get_field('status').remote_field.model, ProductStatus)
        self.assertIs(PurchaseOrder._meta.get_field('status').remote_field.model, TradeStatus)
        self.assertIs(SalesOrder._meta.get_field('status').remote_field.model, TradeStatus)
        unit = UnitOfMeasure.objects.create(name='Pieces', code='STATUS-PCS')
        product = Product.objects.create(name='Product', sku='STATUS', unit=unit, currency_id='BDT')
        purchase = PurchaseOrder.objects.create(currency_id='BDT', exchange_rate='1')
        sale = SalesOrder.objects.create(currency_id='BDT', exchange_rate='1')
        self.assertIsInstance(Product.objects.select_related('status').get(pk=product.pk).status, ProductStatus)
        self.assertIsInstance(PurchaseOrder.objects.select_related('status').get(pk=purchase.pk).status, TradeStatus)
        self.assertIsInstance(SalesOrder.objects.select_related('status').get(pk=sale.pk).status, TradeStatus)
        self.assertEqual(product.status_id, purchase.status_id)
        self.assertEqual(purchase.status_id, sale.status_id)

    def test_inventory_status_api_uses_product_status_permissions_and_both_admins_are_registered(self):
        self.assertTrue(admin.site.is_registered(ProductStatus))
        self.assertTrue(admin.site.is_registered(TradeStatus))
        user = get_user_model().objects.create_user(username='product-status-reader')
        self.client.force_login(user)
        url = reverse('inventory:status-list-view')
        self.assertEqual(self.client.get(url).status_code, 403)
        user.user_permissions.add(Permission.objects.get(content_type__app_label='common', codename='view_tradestatus'))
        self.assertEqual(self.client.get(url).status_code, 403)
        user.user_permissions.add(Permission.objects.get(content_type__app_label='common', codename='view_productstatus'))
        self.assertEqual(self.client.get(url).status_code, 200)
