from warehouse.tests.helpers import assign_warehouse_role
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from time import sleep

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import close_old_connections, connections
from django.test import TransactionTestCase
from rest_framework.exceptions import NotFound
from rest_framework.test import APIRequestFactory, force_authenticate

from common.models import ProductStatus, TradeStatus
from inventory.models import Currency, Product, ProductType, UnitOfMeasure
from purchase.models import PurchaseOrder, PurchaseOrderItem
from purchase.services import PurchaseBusy, receive_purchase
from sale.models import SalesOrder, SalesOrderItem
from sale.services import SaleBusy, complete_sale
from warehouse.access import WarehouseAccessBusy
from warehouse.api_views import RoleWarehouseAccessUpdateAPIView
from warehouse.models import Stock, RoleWarehouseAccess, WareHouse


class WarehouseScopeConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='scoped-worker')
        self.role = Group.objects.create(name="Warehouse operator")
        self.user.groups.set([self.role])
        self.role.permissions.set(Permission.objects.filter(
            codename__in=['change_purchaseorder', 'change_salesorder']))
        self.root = get_user_model().objects.create_superuser(username='scope-manager', email='scope@example.com')
        Currency.objects.get_or_create(code='BDT', defaults={'name': 'Taka', 'exchange_rate': 1})
        ProductType.objects.get_or_create(code='goods', defaults={'name': 'Goods'})
        ProductStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        TradeStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        self.utt = WareHouse.objects.create(name='UTT', address='Dhaka', phone='1')
        self.other = WareHouse.objects.create(name='Other', address='Dhaka', phone='2')
        self.scope = RoleWarehouseAccess.objects.create(role=self.role, warehouse=self.utt)
        unit = UnitOfMeasure.objects.create(name='Pieces', code='SCOPE-RACE')
        self.product = Product.objects.create(sku='SCOPE-RACE', name='Rice', currency_id='BDT', unit=unit)
        self.stock = Stock.objects.create(warehouse=self.utt, product=self.product, quantity=10)

    def race_reassignment(self, model, item_model, parent, service, flag, expected_quantity):
        order = model.objects.create(warehouse=self.utt, currency_id='BDT', exchange_rate=1)
        item_model.objects.create(**{parent: order}, line_number=1, product=self.product,
                                  unit=self.product.unit, quantity=3, unit_price=1)
        barrier = Barrier(2)

        def operation(actor):
            try:
                return service(actor, order.pk)[1]
            except NotFound:
                return False

        def reassign(actor):
            request = APIRequestFactory().patch('/warehouse/access/', {'warehouse': self.other.pk}, format='json')
            force_authenticate(request, actor)
            response = RoleWarehouseAccessUpdateAPIView.as_view()(request, pk=self.scope.pk)
            if response.status_code == 409:
                raise WarehouseAccessBusy()
            self.assertEqual(response.status_code, 200, response.data)
            return True

        def worker(args):
            user_id, action = args
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=user_id)
                actor.get_all_permissions()
                barrier.wait(timeout=10)
                for attempt in range(30):
                    try:
                        return action(actor)
                    except (PurchaseBusy, SaleBusy, WarehouseAccessBusy):
                        if attempt == 29:
                            raise
                        sleep(0.02)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            operated, reassigned = list(pool.map(worker, [(self.user.pk, operation), (self.root.pk, reassign)]))
        self.assertTrue(reassigned)
        self.scope.refresh_from_db()
        order.refresh_from_db()
        self.stock.refresh_from_db()
        self.assertEqual(self.scope.warehouse_id, self.other.pk)
        self.assertEqual(getattr(order, flag), operated)
        self.assertEqual(self.stock.quantity, Decimal(expected_quantity if operated else '10'))
        with self.assertRaises(NotFound):
            service(self.user, order.pk)

    def test_receipt_and_reassignment_serialize_without_unauthorized_stock_changes(self):
        self.race_reassignment(PurchaseOrder, PurchaseOrderItem, 'purchase_order', receive_purchase, 'is_received', '13')

    def test_completion_and_reassignment_serialize_without_unauthorized_stock_changes(self):
        self.race_reassignment(SalesOrder, SalesOrderItem, 'sales_order', complete_sale, 'is_completed', '7')
