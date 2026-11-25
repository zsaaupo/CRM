from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from time import sleep
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import close_old_connections, transaction
from django.db.models import Sum
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from accounting.models import FinancialAccount
from common.exceptions import IdempotencyConflict
from common.models import ProductStatus, SettlementMode, TradeStatus
from inventory.models import Currency, Product, ProductType, UnitOfMeasure
from purchase.models import PurchaseOrder, PurchaseOrderItem
from purchase.services import receive_purchase
from sale.models import SalesOrder, SalesOrderItem, SalesReturn, SalesReturnItem
from sale.returns import complete_return as complete_sale_return
from sale.services import complete_sale
from warehouse.models import Stock, StockMovement, WareHouse
from warehouse.services import post_stock_movement


class Inv01LedgerAndCostingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(username='inv01-admin')
        cls.wh = WareHouse.objects.create(name='INV Main', address='Dhaka', phone='017')
        cls.cash = FinancialAccount.objects.create(
            name='INV Cash', type=FinancialAccount.Type.CASH, warehouse=cls.wh,
            opening_balance=Decimal('10000.0000'),
        )
        cls.unit = UnitOfMeasure.objects.create(name='Pieces', code='INV01-PCS')

    def _new_product(self, sku, cost='10.0000', sale_price='25.0000'):
        return Product.objects.create(
            name=f'Item {sku}', sku=sku, unit=self.unit, currency_id='BDT',
            cost_price=Decimal(cost), sale_price=Decimal(sale_price),
        )

    def test_f_inv_1_ledger_movements_and_reconcile(self):
        product = self._new_product('INV1-SKU', cost='10.0000')
        with transaction.atomic():
            post_stock_movement(
                warehouse_id=self.wh.pk, product_id=product.pk,
                kind=StockMovement.Kind.OPENING, quantity_delta=Decimal('5'),
                unit_cost_base=Decimal('10'), reference_type='opening', reference_id=1,
                posting_key=f'opening:{self.wh.pk}:{product.pk}', actor=self.admin,
            )

        po = PurchaseOrder.objects.create(
            warehouse=self.wh, payment_account=self.cash, currency_id='BDT', exchange_rate=Decimal('1'),
        )
        PurchaseOrderItem.objects.create(
            purchase_order=po, line_number=1, product=product, unit=self.unit,
            quantity=Decimal('10'), unit_price=Decimal('10.0000'),
        )
        receive_purchase(self.admin, po.pk)

        sale = SalesOrder.objects.create(
            warehouse=self.wh, payment_account=self.cash, currency_id='BDT', exchange_rate=Decimal('1'),
        )
        line = SalesOrderItem.objects.create(
            sales_order=sale, line_number=1, product=product, unit=self.unit,
            quantity=Decimal('4'), unit_price=Decimal('25.0000'),
        )
        complete_sale(self.admin, sale.pk)

        ret = SalesReturn.objects.create(sales_order=sale)
        SalesReturnItem.objects.create(sales_return=ret, sales_order_item=line, quantity=Decimal('1'))
        complete_sale_return(self.admin, ret.pk)

        stock = Stock.objects.get(warehouse=self.wh, product=product)
        self.assertEqual(stock.quantity, Decimal('12'))
        ledger_sum = StockMovement.objects.filter(warehouse=self.wh, product=product).aggregate(
            total=Sum('quantity_delta')
        )['total']
        self.assertEqual(ledger_sum, stock.quantity)

        # Duplicate posting_key is idempotent; mismatch raises IdempotencyConflict
        with transaction.atomic():
            again = post_stock_movement(
                warehouse_id=self.wh.pk, product_id=product.pk,
                kind=StockMovement.Kind.OPENING, quantity_delta=Decimal('5'),
                unit_cost_base=Decimal('10'), reference_type='opening', reference_id=1,
                posting_key=f'opening:{self.wh.pk}:{product.pk}', actor=self.admin,
            )
            self.assertEqual(again.quantity_delta, Decimal('5'))
        with self.assertRaises(IdempotencyConflict), transaction.atomic():
            post_stock_movement(
                warehouse_id=self.wh.pk, product_id=product.pk,
                kind=StockMovement.Kind.OPENING, quantity_delta=Decimal('9'),
                unit_cost_base=Decimal('10'), reference_type='opening', reference_id=1,
                posting_key=f'opening:{self.wh.pk}:{product.pk}', actor=self.admin,
            )

        call_command('reconcile_stock_ledger')

    def test_f_inv_2_weighted_average_cost_and_snapshot_isolation(self):
        product = self._new_product('INV2-SKU', cost='10.0000', sale_price='30.0000')
        with transaction.atomic():
            post_stock_movement(
                warehouse_id=self.wh.pk, product_id=product.pk,
                kind=StockMovement.Kind.OPENING, quantity_delta=Decimal('10'),
                unit_cost_base=Decimal('10'), reference_type='opening', reference_id=2,
                posting_key=f'opening:{self.wh.pk}:{product.pk}', actor=self.admin,
            )
        po = PurchaseOrder.objects.create(
            warehouse=self.wh, currency_id='BDT', exchange_rate=Decimal('1'),
            settlement_mode=SettlementMode.DEFERRED,
        )
        PurchaseOrderItem.objects.create(
            purchase_order=po, line_number=1, product=product, unit=self.unit,
            quantity=Decimal('10'), unit_price=Decimal('20.0000'),
        )
        receive_purchase(self.admin, po.pk, settlement_mode=SettlementMode.DEFERRED)
        stock = Stock.objects.get(warehouse=self.wh, product=product)
        self.assertEqual(stock.average_cost, Decimal('15.000000'))

        sale = SalesOrder.objects.create(
            warehouse=self.wh, currency_id='BDT', exchange_rate=Decimal('1'),
            settlement_mode=SettlementMode.DEFERRED,
        )
        s_line = SalesOrderItem.objects.create(
            sales_order=sale, line_number=1, product=product, unit=self.unit,
            quantity=Decimal('5'), unit_price=Decimal('30.0000'),
        )
        complete_sale(self.admin, sale.pk, settlement_mode=SettlementMode.DEFERRED)
        s_line.refresh_from_db()
        self.assertEqual(s_line.unit_cost_base, Decimal('15.000000'))

        # Later catalog cost_price edit leaves posted sale snapshot & return restore unchanged
        product.cost_price = Decimal('99.0000')
        product.save(update_fields=['cost_price'])
        s_line.refresh_from_db()
        self.assertEqual(s_line.unit_cost_base, Decimal('15.000000'))

        ret = SalesReturn.objects.create(sales_order=sale)
        SalesReturnItem.objects.create(sales_return=ret, sales_order_item=s_line, quantity=Decimal('2'))
        complete_sale_return(self.admin, ret.pk)
        ret_move = StockMovement.objects.get(reference_type='sales_return', reference_id=ret.pk)
        self.assertEqual(ret_move.unit_cost_base, Decimal('15.000000'))

    def test_no_direct_stock_writes(self):
        base = Path(settings.BASE_DIR)
        pattern = re.compile(r'\bStock\.objects\.[^;\n]*\.update\([^)]*quantity\s*=')
        offenders = []
        for app_dir in ('account', 'accounting', 'common', 'hrm', 'inventory', 'people', 'purchase', 'report', 'sale', 'warehouse'):
            for py_file in (base / app_dir).rglob('*.py'):
                rel = py_file.relative_to(base).as_posix()
                if '/migrations/' in rel or '/tests/' in rel or rel == 'warehouse/services.py' or rel.endswith('tests.py'):
                    continue
                text = py_file.read_text(encoding='utf-8')
                if pattern.search(text):
                    offenders.append(rel)
        self.assertEqual(offenders, [], f'Direct Stock.quantity updates found outside warehouse/services.py: {offenders}')


class Inv01ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username='inv-race-admin')
        Currency.objects.get_or_create(code='BDT', defaults={'name': 'Taka', 'exchange_rate': 1})
        ProductType.objects.get_or_create(code='goods', defaults={'name': 'Goods'})
        ProductStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        TradeStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        self.wh = WareHouse.objects.create(name='INV Race WH', address='Dhaka', phone='01')
        unit = UnitOfMeasure.objects.create(name='Pieces', code='INV-RACE-PCS')
        self.product = Product.objects.create(name='INV Race', sku='INV-RACE-SKU', unit=unit, currency_id='BDT')
        with transaction.atomic():
            post_stock_movement(
                warehouse_id=self.wh.pk, product_id=self.product.pk,
                kind=StockMovement.Kind.OPENING, quantity_delta=Decimal('1'),
                unit_cost_base=Decimal('10'), reference_type='opening', reference_id=1,
                posting_key=f'opening:{self.wh.pk}:{self.product.pk}', actor=self.admin,
            )
        self.s1 = SalesOrder.objects.create(warehouse=self.wh, currency_id='BDT', exchange_rate=1, settlement_mode=SettlementMode.DEFERRED)
        self.s2 = SalesOrder.objects.create(warehouse=self.wh, currency_id='BDT', exchange_rate=1, settlement_mode=SettlementMode.DEFERRED)
        SalesOrderItem.objects.create(sales_order=self.s1, line_number=1, product=self.product, unit=unit, quantity=1, unit_price=10)
        SalesOrderItem.objects.create(sales_order=self.s2, line_number=1, product=self.product, unit=unit, quantity=1, unit_price=10)

    def test_two_simultaneous_completions_for_last_unit_one_wins(self):
        barrier = Barrier(2)
        results = []

        def worker(order_id):
            close_old_connections()
            actor = get_user_model().objects.get(pk=self.admin.pk)
            barrier.wait(timeout=10)
            for _ in range(30):
                try:
                    _, done = complete_sale(actor, order_id, settlement_mode=SettlementMode.DEFERRED)
                    results.append(('ok', order_id, done))
                    return
                except Exception as exc:
                    if 'locked' in str(exc).lower() or 'busy' in str(exc).lower():
                        sleep(0.02)
                        continue
                    results.append(('err', order_id, str(exc)))
                    return
            results.append(('err', order_id, 'timeout'))

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(worker, self.s1.pk)
            f2 = pool.submit(worker, self.s2.pk)
            f1.result()
            f2.result()

        oks = [r for r in results if r[0] == 'ok']
        errs = [r for r in results if r[0] == 'err']
        self.assertEqual(len(oks), 1, results)
        self.assertEqual(len(errs), 1, results)
        self.assertEqual(Stock.objects.get(warehouse=self.wh, product=self.product).quantity, Decimal('0'))
        self.assertEqual(StockMovement.objects.filter(warehouse=self.wh, product=self.product, kind=StockMovement.Kind.SALE_ISSUE).count(), 1)
