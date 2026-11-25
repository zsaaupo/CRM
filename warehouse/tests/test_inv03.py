from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from time import sleep
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import close_old_connections, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from common.models import ProductStatus, SettlementMode, TradeStatus
from inventory.models import Brand, Currency, Product, ProductCategory, ProductType, TaxRule, UnitOfMeasure
from sale.models import SalesOrder, SalesOrderItem
from sale.services import complete_sale
from warehouse.adjustments import post_stock_adjustment
from warehouse.counts import create_stock_count, finalize_stock_count, import_count_final_file
from warehouse.models import (
    RoleWarehouseAccess, Stock, StockAdjustment, StockAdjustmentItem, StockCount, StockMovement,
    StockSerial, WareHouse, WarehouseTransfer, WarehouseTransferItem,
)
from warehouse.opening import post_opening_stock
from warehouse.services import dispatch_transfer, receive_transfer


class Inv03Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(username='inv03-admin', password='pw')
        cls.wh_a = WareHouse.objects.create(name='INV03 WH A', address='Dhaka', phone='01')
        cls.wh_b = WareHouse.objects.create(name='INV03 WH B', address='Khulna', phone='02')
        cls.pcs = UnitOfMeasure.objects.create(name='Piece', code='INV3-PCS')
        cls.box = UnitOfMeasure.objects.create(
            name='Box', code='INV3-BOX', base_unit=cls.pcs,
            operator=UnitOfMeasure.Operator.MULTIPLY, operation_value=Decimal('10'),
        )
        cls.prod = Product.objects.create(
            sku='INV3-PROD', name='Count Widget', unit=cls.pcs, currency_id='BDT',
            cost_price=Decimal('10'), sale_price=Decimal('25'),
        )
        cls.tracked = Product.objects.create(
            sku='INV3-SER', name='Tracked Device', unit=cls.pcs, currency_id='BDT',
            cost_price=Decimal('100'), sale_price=Decimal('150'), has_imei_serial=True,
        )
        post_opening_stock(
            cls.admin,
            [
                {'warehouse': cls.wh_a.pk, 'product': cls.prod.pk, 'quantity': '12', 'unit_cost_base': '10'},
                {'warehouse': cls.wh_a.pk, 'product': cls.tracked.pk, 'quantity': '2', 'unit_cost_base': '100', 'serials': ['SN-A1', 'SN-A2']},
            ],
            uuid4(),
        )

    def test_f_inv_4_and_f_inv_6_stock_count_and_adjustment(self):
        client = APIClient()
        client.force_login(self.admin)

        # Pages return 200
        self.assertEqual(client.get(reverse('warehouse_pages:adjustment-list')).status_code, 200)
        self.assertEqual(client.get(reverse('warehouse_pages:stock-count-list')).status_code, 200)

        # Create count (expected 12 on INV3-PROD)
        c_resp = client.post(reverse('warehouse:stock-count-list'), {'warehouse': self.wh_a.pk, 'count_type': 'full'}, format='json')
        self.assertEqual(c_resp.status_code, 201, c_resp.data)
        sc_id = c_resp.data['id']

        # Initial CSV downloadable
        init_csv = client.get(reverse('warehouse:stock-count-initial-file', args=[sc_id])).content.decode('utf-8-sig')
        self.assertIn('INV3-PROD', init_csv)

        # Bad final CSV (unknown SKU + negative qty) rejects whole file
        bad_final = 'sku,name,batch,expected_qty,counted_qty\nNO-SKU,Bad,,12,5\nINV3-PROD,Count Widget,,-1,-1\n'
        self.assertEqual(client.post(reverse('warehouse:stock-count-final-file', args=[sc_id]), {'csv': bad_final}, format='json').status_code, 400)

        # F-INV-4: expected 12, counted 10 -> one ADJUSTMENT_SUB of -2
        good_final = 'sku,name,batch,expected_qty,counted_qty\nINV3-PROD,Count Widget,,12,10\nINV3-SER,Tracked Device,,2,2\n'
        f_resp = client.post(reverse('warehouse:stock-count-final-file', args=[sc_id]), {'csv': good_final}, format='json')
        self.assertEqual(f_resp.status_code, 200, f_resp.data)

        key_fin = str(uuid4())
        fin = client.post(reverse('warehouse:stock-count-finalize', args=[sc_id]), {'request_key': key_fin}, format='json')
        self.assertEqual(fin.status_code, 200, fin.data)
        self.assertEqual(fin.data['status'], StockCount.Status.FINALIZED)
        self.assertIsNotNone(fin.data['adjustment'])
        self.assertEqual(Stock.objects.get(warehouse=self.wh_a, product=self.prod).quantity, Decimal('10'))
        self.assertEqual(
            StockMovement.objects.filter(
                kind=StockMovement.Kind.ADJUSTMENT_SUB, reference_type='stock_adjustment', reference_id=fin.data['adjustment'],
            ).get().quantity_delta,
            Decimal('-2'),
        )

        # Replay finalize -> zero extra movement
        self.assertEqual(client.post(reverse('warehouse:stock-count-finalize', args=[sc_id]), {'request_key': key_fin}, format='json').status_code, 200)
        self.assertEqual(Stock.objects.get(warehouse=self.wh_a, product=self.prod).quantity, Decimal('10'))

        # F-INV-6: Count snapshot 10, adjust +2 to get back to 12 first, then snapshot at 12
        adj_add = StockAdjustment.objects.create(warehouse=self.wh_a, reason='Restore 12')
        StockAdjustmentItem.objects.create(
            adjustment=adj_add, product=self.prod, action=StockAdjustmentItem.Action.ADDITION,
            quantity=2, unit=self.pcs, unit_cost=10,
        )
        # Draft adjustment created zero movements
        self.assertEqual(Stock.objects.get(warehouse=self.wh_a, product=self.prod).quantity, Decimal('10'))
        post_stock_adjustment(self.admin, adj_add.pk, uuid4())
        self.assertEqual(Stock.objects.get(warehouse=self.wh_a, product=self.prod).quantity, Decimal('12'))

        sc2 = create_stock_count(self.admin, self.wh_a.pk, 'full')
        # Ensure sale StockMovement posted_at > sc2.snapshot_at
        sleep(0.02)
        so = SalesOrder.objects.create(warehouse=self.wh_a, currency_id='BDT', exchange_rate=1, settlement_mode=SettlementMode.DEFERRED)
        SalesOrderItem.objects.create(sales_order=so, line_number=1, product=self.prod, unit=self.pcs, quantity=Decimal('2'), unit_price=25)
        complete_sale(self.admin, so.pk, settlement_mode=SettlementMode.DEFERRED)

        import_count_final_file(self.admin, sc2.pk, 'sku,name,batch,expected_qty,counted_qty\nINV3-PROD,Count Widget,,12,10\nINV3-SER,Tracked Device,,2,2\n')
        conflict = client.post(
            reverse('warehouse:stock-count-finalize', args=[sc2.pk]),
            {'request_key': str(uuid4()), 'reconcile_intervening': False}, format='json',
        )
        self.assertEqual(conflict.status_code, 409, conflict.data)
        self.assertTrue(conflict.data.get('needs_recount'))

        reconciled = client.post(
            reverse('warehouse:stock-count-finalize', args=[sc2.pk]),
            {'request_key': str(uuid4()), 'reconcile_intervening': True}, format='json',
        )
        self.assertEqual(reconciled.status_code, 200, reconciled.data)
        self.assertIsNone(reconciled.data['adjustment'])  # variance = 10 - (12 - 2) = 0 -> no adjustment
        self.assertEqual(Stock.objects.get(warehouse=self.wh_a, product=self.prod).quantity, Decimal('10'))

    def test_f_inv_5_staged_transfer_scope_serials_csv_and_chart(self):
        # Role with access only to WH_B + receive_transfer cannot dispatch WH_A -> WH_B, can receive it!
        role_a = Group.objects.create(name='inv03-disp-a')
        RoleWarehouseAccess.objects.create(role=role_a, warehouse=self.wh_a, all_warehouses=False)
        role_a.permissions.add(*Permission.objects.filter(codename__in=['dispatch_transfer', 'view_warehousetransfer']))
        user_a = get_user_model().objects.create_user(username='inv03-a', password='pw')
        user_a.groups.add(role_a)

        role_b = Group.objects.create(name='inv03-recv-b')
        RoleWarehouseAccess.objects.create(role=role_b, warehouse=self.wh_b, all_warehouses=False)
        role_b.permissions.add(*Permission.objects.filter(codename__in=['receive_transfer', 'view_warehousetransfer', 'view_stock']))
        user_b = get_user_model().objects.create_user(username='inv03-b', password='pw')
        user_b.groups.add(role_b)

        tr = WarehouseTransfer.objects.create(source_warehouse=self.wh_a, destination_warehouse=self.wh_b)
        WarehouseTransferItem.objects.create(transfer=tr, line_number=1, product=self.prod, unit=self.pcs, quantity=Decimal('3'))
        WarehouseTransferItem.objects.create(
            transfer=tr, line_number=2, product=self.tracked, unit=self.pcs,
            quantity=Decimal('1'), serial_numbers=['SN-A1'],
        )

        client_a = APIClient()
        client_a.force_login(user_a)
        client_b = APIClient()
        client_b.force_login(user_b)

        # User B (destination only) cannot dispatch from source WH A
        self.assertIn(client_b.post(reverse('warehouse:transfer-dispatch', args=[tr.pk])).status_code, (403, 404))

        # User A dispatches -> A -3 (-1 tracked), in-transit 3 (+1 tracked), serial SN-A1 IN_TRANSIT
        disp = client_a.post(reverse('warehouse:transfer-dispatch', args=[tr.pk]))
        self.assertEqual(disp.status_code, 200, disp.data)
        self.assertEqual(disp.data['status'], WarehouseTransfer.Status.SENT)
        self.assertEqual(Stock.objects.get(warehouse=self.wh_a, product=self.prod).quantity, Decimal('9'))
        self.assertEqual(StockSerial.objects.get(serial_number='SN-A1').status, StockSerial.Status.IN_TRANSIT)

        # Chart shows current valuation and in_transit_qty at destination WH B
        chart_res = client_b.get(reverse('warehouse:stock-chart')).data
        self.assertEqual(chart_res['series_type'], 'current')
        self.assertEqual(Decimal(chart_res['warehouses'][0]['in_transit_qty']), Decimal('4'))  # 3 prod + 1 tracked

        # User A (source only) cannot receive at destination WH B
        self.assertIn(client_a.post(reverse('warehouse:transfer-receive', args=[tr.pk])).status_code, (403, 404))

        # User B receives -> B +3, in-transit 0, serial SN-A1 back IN_STOCK at WH B
        recv1 = client_b.post(reverse('warehouse:transfer-receive', args=[tr.pk]))
        self.assertEqual(recv1.status_code, 200, recv1.data)
        self.assertEqual(recv1.data['status'], WarehouseTransfer.Status.COMPLETED)
        self.assertTrue(recv1.data['received_now'])
        self.assertEqual(Stock.objects.get(warehouse=self.wh_b, product=self.prod).quantity, Decimal('3'))
        s_a1 = StockSerial.objects.get(serial_number='SN-A1')
        self.assertEqual((s_a1.status, s_a1.warehouse_id), (StockSerial.Status.IN_STOCK, self.wh_b.pk))

        # Duplicate receive -> zero effect
        recv2 = client_b.post(reverse('warehouse:transfer-receive', args=[tr.pk]))
        self.assertEqual(recv2.status_code, 200)
        self.assertFalse(recv2.data['received_now'])
        self.assertEqual(Stock.objects.get(warehouse=self.wh_b, product=self.prod).quantity, Decimal('3'))

        # Transfer CSV import
        TaxRule.objects.create(name='TR10', rate=Decimal('10'))
        admin_client = APIClient()
        admin_client.force_login(self.admin)
        csv_body = 'product_code,quantity,product_unit,product_cost,tax_name\nINV3-PROD,1,INV3-BOX,100,TR10\n'
        imp = admin_client.post(
            reverse('warehouse:transfer-import-csv'),
            {
                'source_warehouse': self.wh_a.pk, 'destination_warehouse': self.wh_b.pk,
                'status': 'pending', 'shipping_cost': '15', 'csv': csv_body,
            },
            format='json',
        )
        self.assertEqual(imp.status_code, 201, imp.data)
        self.assertEqual(imp.data['status'], WarehouseTransfer.Status.PENDING)
        self.assertEqual(Decimal(imp.data['items'][0]['base_quantity']), Decimal('10'))


class Inv03ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username='inv3-race-admin')
        Currency.objects.get_or_create(code='BDT', defaults={'name': 'Taka', 'exchange_rate': 1})
        ProductType.objects.get_or_create(code='goods', defaults={'name': 'Goods'})
        ProductStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        TradeStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        self.wh = WareHouse.objects.create(name='INV3 Race', address='Dhaka', phone='01')
        self.pcs = UnitOfMeasure.objects.create(name='Piece', code='INV3-RACE-PCS')
        self.prod = Product.objects.create(name='Race Widget', sku='INV3-RACE', unit=self.pcs, currency_id='BDT', cost_price=10)
        post_opening_stock(self.admin, [{'warehouse': self.wh.pk, 'product': self.prod.pk, 'quantity': '10'}], uuid4())
        self.sc = create_stock_count(self.admin, self.wh.pk, 'full')
        sleep(0.02)
        import_count_final_file(self.admin, self.sc.pk, 'sku,name,batch,expected_qty,counted_qty\nINV3-RACE,Race Widget,,10,9\n')
        self.so = SalesOrder.objects.create(warehouse=self.wh, currency_id='BDT', exchange_rate=1, settlement_mode=SettlementMode.DEFERRED)
        SalesOrderItem.objects.create(sales_order=self.so, line_number=1, product=self.prod, unit=self.pcs, quantity=2, unit_price=20)

    def test_sale_during_count_cannot_vanish_on_finalize(self):
        # Complete sale concurrently or immediately before finalize -> finalize detects movement and returns needs_recount!
        complete_sale(self.admin, self.so.pk, settlement_mode=SettlementMode.DEFERRED)
        with self.assertRaises(Exception) as ctx:
            finalize_stock_count(self.admin, self.sc.pk, uuid4(), reconcile_intervening=False)
        self.assertIn('needs_recount', str(ctx.exception))
        self.assertEqual(Stock.objects.get(warehouse=self.wh, product=self.prod).quantity, Decimal('8'))
