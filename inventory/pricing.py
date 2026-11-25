"""Unified price, promo, warehouse price, customer group, and tax resolver (D-23)."""

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import F, Q
from django.utils import timezone

from common.models import TaxMethod
from common.money import q_money
from inventory.models import Product, TaxRule, WarehouseProductPrice


ZERO = Decimal('0')


@dataclass(frozen=True)
class ResolvedPrice:
    price: Decimal
    source: str
    rule_id: int | None
    tax_rule: TaxRule | None
    tax_rate: Decimal
    tax_method: str
    wholesale_price: Decimal | None = None


def _active_promo(product: Product, doc_date) -> Decimal | None:
    if not product.is_promo or product.promo_price is None:
        return None
    if product.promo_start_date and doc_date < product.promo_start_date:
        return None
    if product.promo_end_date and doc_date > product.promo_end_date:
        return None
    return q_money(product.promo_price, digits=4)


def _warehouse_price(product: Product, warehouse_id: int | None, doc_date) -> WarehouseProductPrice | None:
    if not warehouse_id:
        return None
    return (
        WarehouseProductPrice.objects.filter(warehouse_id=warehouse_id, product_id=product.pk)
        .filter(Q(effective_from__isnull=True) | Q(effective_from__lte=doc_date))
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=doc_date))
        .order_by(F('effective_from').desc(nulls_last=True), '-pk')
        .first()
    )


def _resolve_discount_plan_price(product: Product, customer, doc_date, quantity: Decimal, base_price: Decimal):
    """Hook extended by PRICE-01 discount plans."""
    try:
        from sale import pricing_rules as _pr
        return _pr.best_discount_plan_price(product, customer, doc_date, quantity, base_price)
    except ImportError:
        return None


def resolve_unit_price(
    product: Product,
    warehouse=None,
    customer=None,
    date=None,
    quantity: Decimal = Decimal('1'),
    manual_price=None,
) -> ResolvedPrice:
    doc_date = date or timezone.localdate()
    wh_id = warehouse.pk if hasattr(warehouse, 'pk') else warehouse
    wh_row = _warehouse_price(product, wh_id, doc_date)

    tax_rule = product.tax_rule
    tax_rate = Decimal(tax_rule.rate) if tax_rule is not None else Decimal(product.product_tax or ZERO)
    tax_method = product.tax_method or TaxMethod.EXCLUSIVE
    wholesale = (
        wh_row.wholesale_price
        if wh_row and wh_row.wholesale_price is not None
        else product.Whole_sale_price
    )

    # 1. Manual override
    if manual_price is not None:
        return ResolvedPrice(
            price=q_money(manual_price, digits=4),
            source='manual',
            rule_id=None,
            tax_rule=tax_rule,
            tax_rate=tax_rate,
            tax_method=tax_method,
            wholesale_price=wholesale,
        )

    # 2. Active product promo
    promo = _active_promo(product, doc_date)
    if promo is not None:
        return ResolvedPrice(
            price=promo,
            source='promo',
            rule_id=product.pk,
            tax_rule=tax_rule,
            tax_rate=tax_rate,
            tax_method=tax_method,
            wholesale_price=wholesale,
        )

    base_price = q_money(wh_row.sale_price, digits=4) if (wh_row and wh_row.sale_price is not None) else q_money(product.sale_price, digits=4)

    # 3. Discount plans (PRICE-01)
    plan_match = _resolve_discount_plan_price(product, customer, doc_date, Decimal(quantity), base_price)
    if plan_match is not None:
        plan_price, plan_id = plan_match
        return ResolvedPrice(
            price=q_money(plan_price, digits=4),
            source='discount_plan',
            rule_id=plan_id,
            tax_rule=tax_rule,
            tax_rate=tax_rate,
            tax_method=tax_method,
            wholesale_price=wholesale,
        )

    # 4. Warehouse override price
    if wh_row and wh_row.sale_price is not None:
        return ResolvedPrice(
            price=q_money(wh_row.sale_price, digits=4),
            source='warehouse_price',
            rule_id=wh_row.pk,
            tax_rule=tax_rule,
            tax_rate=tax_rate,
            tax_method=tax_method,
            wholesale_price=wholesale,
        )

    # 5. Customer group tier discount on base product sale_price (or wholesale basis)
    if customer is not None and getattr(customer, 'customer_group_id', None):
        group = customer.customer_group
        basis_price = base_price
        if getattr(group, 'price_basis', None) == 'wholesale' and wholesale is not None:
            basis_price = q_money(wholesale, digits=4)
        pct = Decimal(getattr(group, 'percentage', ZERO) or ZERO)
        if pct != ZERO or basis_price != base_price:
            discounted = q_money(basis_price * (Decimal('1') - pct / Decimal('100')), digits=4)
            return ResolvedPrice(
                price=max(discounted, ZERO),
                source='customer_group',
                rule_id=group.pk,
                tax_rule=tax_rule,
                tax_rate=tax_rate,
                tax_method=tax_method,
                wholesale_price=wholesale,
            )

    return ResolvedPrice(
        price=base_price,
        source='product',
        rule_id=None,
        tax_rule=tax_rule,
        tax_rate=tax_rate,
        tax_method=tax_method,
        wholesale_price=wholesale,
    )


def resolve_alert_threshold(product: Product, warehouse=None, doc_date=None) -> Decimal | None:
    wh_id = warehouse.pk if hasattr(warehouse, 'pk') else warehouse
    wh_row = _warehouse_price(product, wh_id, doc_date or timezone.localdate())
    if wh_row and wh_row.alert_quantity is not None:
        return wh_row.alert_quantity
    return product.alert_quantity
