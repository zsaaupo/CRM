from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from common.exceptions import InsufficientStock, StateConflict
from common.settings_store import get_general, invalidate_general_cache
from inventory.models import Currency, Product, ProductCategory, ProductType, UnitOfMeasure
from manufacturing.models import (
    BOMComponent, BillOfMaterials, ProductionOrder, RawMaterial, RawMaterialMovement, RawMaterialStock,
)
from manufacturing.services import (
    approve_bom, compute_component_required_qty, post_production_order,
    receive_raw_material, save_production_draft,
)
from warehouse.models import RoleWarehouseAccess, Stock, StockMovement, WareHouse


User = get_user_model()


class MFG01Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(username='mfg_su', email='mfg@example.com', password='pass')
        cls.role = Group.objects.create(name='Production Ops')
        cls.limited_user = User.objects.create_user(username='mfg_lim', email='lim@example.com', password='pass')
        cls.limited_user.groups.add(cls.role)

        cls.wh_a = WareHouse.objects.create(name='Plant WH', code='PLANT', address='Dhaka', phone='01700000001')
        cls.wh_b = WareHouse.objects.create(name='Other WH', code='OTHER', address='Chattogram', phone='01700000002')
        RoleWarehouseAccess.objects.create(role=cls.role, all_warehouses=False, warehouse=cls.wh_a)

        cls.kg = UnitOfMeasure.objects.create(name='Kilogram', code='KG')
        cls.pcs = UnitOfMeasure.objects.create(name='Piece', code='PCS')
        cls.cat = ProductCategory.objects.create(name='Bakery')
        cls.bdt, _ = Currency.objects.get_or_create(code='BDT', defaults={'name': 'Taka', 'symbol': '৳', 'exchange_rate': Decimal('1')})
        cls.ptype_goods, _ = ProductType.objects.get_or_create(code='goods', defaults={'name': 'Standard Goods'})

        cls.finished = Product.objects.create(
            name='Loaf Bread',
            sku='BREAD-01',
            product_type=cls.ptype_goods,
            supply_method='in_house_production',
            category=cls.cat,
            unit=cls.pcs,
            currency=cls.bdt,
            cost_price=Decimal('20.0000'),
            sale_price=Decimal('50.0000'),
        )
        cls.flour = RawMaterial.objects.create(
            code='RM-FLOUR',
            name='Wheat Flour',
            category=cls.cat,
            stock_unit=cls.kg,
            default_warehouse=cls.wh_a,
        )

        gs = get_general()
        gs.enable_in_house_production = True
        gs.save(update_fields=['enable_in_house_production'])
        invalidate_general_cache()

    def test_f_mfg_1_bom_wastage_net_gross_and_issue_multiple(self):
        bom = BillOfMaterials.objects.create(
            code='BOM-F1', name='Loaf Recipe', finished_product=self.finished,
            version=1, base_output_qty=Decimal('10'),
        )
        comp = BOMComponent.objects.create(
            bom=bom, raw_material=self.flour, unit=self.kg,
            quantity_per_base_output=Decimal('4'),
            wastage_percent=Decimal('10'),
            wastage_basis=BOMComponent.WastageBasis.NET,
            issue_multiple=None,
        )
        self.assertEqual(compute_component_required_qty(comp, Decimal('10'), Decimal('20')), Decimal('8.800000'))

        comp.wastage_basis = BOMComponent.WastageBasis.GROSS
        comp.save(update_fields=['wastage_basis'])
        self.assertEqual(compute_component_required_qty(comp, Decimal('10'), Decimal('20')), Decimal('8.888889'))

        comp.issue_multiple = Decimal('0.5')
        comp.save(update_fields=['issue_multiple'])
        self.assertEqual(compute_component_required_qty(comp, Decimal('10'), Decimal('20')), Decimal('9.000000'))

    def test_f_mfg_2_production_cost_rollback_idempotency_and_bom_freeze(self):
        receive_raw_material(
            self.superuser, self.flour.pk, self.wh_a.pk,
            Decimal('10'), Decimal('50'), 'opening', uuid.uuid4(),
        )

        bom = BillOfMaterials.objects.create(
            code='BOM-F2', name='Loaf Batch', finished_product=self.finished,
            version=2, base_output_qty=Decimal('10'),
        )
        BOMComponent.objects.create(
            bom=bom, raw_material=self.flour, unit=self.kg,
            quantity_per_base_output=Decimal('4'), wastage_percent=Decimal('0'),
        )
        approve_bom(self.superuser, bom.pk)

        order = save_production_draft(
            self.superuser,
            bom_id=bom.pk,
            material_warehouse_id=self.wh_a.pk,
            finished_warehouse_id=self.wh_a.pk,
            planned_qty=Decimal('20'),
            accepted_good_qty=Decimal('18'),
            rejected_qty=Decimal('2'),
            labor_cost=Decimal('80'),
            overhead_cost=Decimal('60'),
        )
        req_key = uuid.uuid4()
        posted, created = post_production_order(self.superuser, order.pk, req_key)
        self.assertTrue(created)
        self.assertEqual(posted.status, ProductionOrder.Status.POSTED)
        self.assertEqual(posted.material_cost_total, Decimal('400.0000'))
        self.assertEqual(posted.unit_cost_calculated, Decimal('30.000000'))

        rm_stock = RawMaterialStock.objects.get(warehouse=self.wh_a, raw_material=self.flour)
        self.assertEqual(rm_stock.quantity, Decimal('2.000000'))
        fg_stock = Stock.objects.get(warehouse=self.wh_a, product=self.finished)
        self.assertEqual(fg_stock.quantity, Decimal('18.000000'))
        self.assertEqual(fg_stock.average_cost, Decimal('30.000000'))

        # Idempotent replay does not double-post raw material or finished goods
        replay, created2 = post_production_order(self.superuser, order.pk, req_key)
        self.assertFalse(created2)
        self.assertEqual(replay.pk, posted.pk)
        rm_stock.refresh_from_db()
        fg_stock.refresh_from_db()
        self.assertEqual(rm_stock.quantity, Decimal('2.000000'))
        self.assertEqual(fg_stock.quantity, Decimal('18.000000'))

        # Approved BOM used by a production order is frozen from edits
        client = APIClient()
        client.force_authenticate(user=self.superuser)
        res = client.patch(
            reverse('manufacturing:bom-detail', args=[bom.pk]),
            {'name': 'Tampered BOM'},
            format='json',
        )
        self.assertEqual(res.status_code, 409)

        # Shortage at commit rolls back cleanly (2 kg left, order needs 8 kg)
        order_short = save_production_draft(
            self.superuser,
            bom_id=bom.pk,
            material_warehouse_id=self.wh_a.pk,
            finished_warehouse_id=self.wh_a.pk,
            planned_qty=Decimal('20'),
            accepted_good_qty=Decimal('20'),
            rejected_qty=Decimal('0'),
        )
        mv_count_before = RawMaterialMovement.objects.count()
        sm_count_before = StockMovement.objects.count()
        with self.assertRaises(InsufficientStock):
            post_production_order(self.superuser, order_short.pk, uuid.uuid4())
        rm_stock.refresh_from_db()
        fg_stock.refresh_from_db()
        order_short.refresh_from_db()
        self.assertEqual(rm_stock.quantity, Decimal('2.000000'))
        self.assertEqual(fg_stock.quantity, Decimal('18.000000'))
        self.assertEqual(order_short.status, ProductionOrder.Status.DRAFT)
        self.assertEqual(RawMaterialMovement.objects.count(), mv_count_before)
        self.assertEqual(StockMovement.objects.count(), sm_count_before)

        # All-rejected batch is blocked before posting
        order_zero = save_production_draft(
            self.superuser,
            bom_id=bom.pk,
            material_warehouse_id=self.wh_a.pk,
            finished_warehouse_id=self.wh_a.pk,
            planned_qty=Decimal('2'),
            accepted_good_qty=Decimal('0'),
            rejected_qty=Decimal('2'),
        )
        with self.assertRaises(Exception):
            post_production_order(self.superuser, order_zero.pk, uuid.uuid4())

    def test_scope_enforcement_on_preview_and_switch_on_purchase_add(self):
        bom = BillOfMaterials.objects.create(
            code='BOM-SCOPE', name='Scope BOM', finished_product=self.finished,
            version=3, base_output_qty=Decimal('10'),
        )
        BOMComponent.objects.create(
            bom=bom, raw_material=self.flour, unit=self.kg,
            quantity_per_base_output=Decimal('4'),
        )
        approve_bom(self.superuser, bom.pk)

        client = APIClient()
        client.force_authenticate(user=self.limited_user)
        res = client.post(
            reverse('manufacturing:production-preview'),
            {'bom': bom.pk, 'material_warehouse': self.wh_b.pk, 'planned_qty': '10'},
            format='json',
        )
        self.assertIn(res.status_code, (403, 404))

        client.force_authenticate(user=self.superuser)
        client.force_login(self.superuser)
        page_res = client.get(reverse('purchase_pages:create-purchase'))
        self.assertEqual(page_res.status_code, 200)
        self.assertTrue(page_res.context['enable_in_house_production'])
