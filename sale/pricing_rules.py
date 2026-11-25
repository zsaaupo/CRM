"""Customer/product/category discount plan rule evaluation."""

from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from common.models import DiscountType
from common.money import q_money
from .models import Discount


ZERO = Decimal('0')


def apply_discount_rules(customer, product, qty: Decimal, date=None, base_price: Decimal | None = None):
    """Pick the best eligible Discount rule; return (price, rule_id, rule_name) or None."""
    doc_date = date or timezone.localdate()
    weekday = doc_date.weekday()
    base = Decimal(base_price if base_price is not None else product.sale_price)
    q = Decimal(qty)

    plan_ids = set()
    if customer is not None:
        if getattr(customer, 'discount_plan_id', None):
            plan_ids.add(customer.discount_plan_id)
        plan_ids.update(customer.discount_plans_m2m.filter(is_active=True).values_list('pk', flat=True))
        if getattr(customer, 'customer_group_id', None):
            plan_ids.update(customer.customer_group.discount_plans.filter(is_active=True).values_list('pk', flat=True))

    if not plan_ids:
        return None

    rules = (
        Discount.objects.filter(
            plan_id__in=plan_ids,
            plan__is_active=True,
            is_active=True,
            valid_from__lte=doc_date,
            valid_till__gte=doc_date,
            minimum_qty__lte=q,
        )
        .filter(Q(maximum_qty__isnull=True) | Q(maximum_qty__gte=q))
        .prefetch_related('products', 'categories')
        .order_by('pk')
    )

    best = None  # (discounted_price, rule_id, rule_name)
    for rule in rules:
        if rule.days_of_week and weekday not in rule.days_of_week:
            continue
        if rule.applies_to == Discount.AppliesTo.PRODUCTS:
            if not any(p.pk == product.pk for p in rule.products.all()):
                continue
        elif rule.applies_to == Discount.AppliesTo.CATEGORIES:
            if not product.category_id or not any(c.pk == product.category_id for c in rule.categories.all()):
                continue

        if rule.discount_type == DiscountType.PERCENTAGE:
            price = q_money(max(base * (Decimal('1') - Decimal(rule.value) / Decimal('100')), ZERO), digits=4)
        else:
            price = q_money(max(base - Decimal(rule.value), ZERO), digits=4)

        if best is None or price < best[0] or (price == best[0] and rule.pk < best[1]):
            best = (price, rule.pk, rule.name)

    return best


def best_discount_plan_price(product, customer, doc_date, qty: Decimal, base_price: Decimal):
    match = apply_discount_rules(customer, product, qty, doc_date, base_price)
    if match is None:
        return None
    return match[0], match[1]
