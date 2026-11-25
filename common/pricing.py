from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext

from django.core.exceptions import ValidationError

from .money import ZERO, allocate_pro_rata, q_money


@dataclass(frozen=True)
class PricedLine:
    ex_tax: Decimal
    line_tax: Decimal
    allocated_order_discount: Decimal
    allocated_order_tax: Decimal
    net_effective: Decimal
    returnable_total: Decimal


@dataclass(frozen=True)
class DocumentTotals:
    pre_discount_subtotal: Decimal
    order_discount_amount: Decimal
    taxable_subtotal: Decimal
    line_tax_total: Decimal
    order_tax_amount: Decimal
    shipping_cost: Decimal
    grand_total: Decimal
    lines: list[PricedLine]


def calculate_document_totals(
    lines,
    *,
    order_discount_type='flat',
    order_discount_value=ZERO,
    order_tax_rate=ZERO,
    shipping_cost=ZERO,
    digits=4,
) -> DocumentTotals:
    """
    Shared line + header pricing math (D-20, D-21, D-22):
    - Line tax exclusive: tax = line_net * rate / 100
    - Line tax inclusive: base = line_net / (1 + rate / 100), tax = line_net - base
    - Header order_discount applies on post-line-discount ex-tax subtotal, before order_tax
    - Allocated pro-rata with rounding residual to last positive-weight line
    - order_tax is additive on taxable_subtotal; shipping is untaxed & non-discountable
    """
    ex_tax_list = []
    line_tax_list = []

    with localcontext() as ctx:
        ctx.prec = 50
        for line in lines:
            qty = Decimal(str(line.get('quantity', 0)))
            price = Decimal(str(line.get('unit_price', 0)))
            line_disc = Decimal(str(line.get('discount_amount', 0)))
            tax_rate = Decimal(str(line.get('tax_rate', 0)))
            tax_method = line.get('tax_method', 'exclusive') or 'exclusive'

            gross = qty * price
            net_raw = gross - line_disc
            if net_raw < ZERO:
                raise ValidationError({'discount_amount': 'Line discount cannot exceed line gross total.'})

            if tax_method == 'inclusive' and tax_rate > ZERO:
                net_q = q_money(net_raw, digits=digits)
                base = q_money(net_raw / (Decimal('1') + tax_rate / Decimal('100')), digits=digits)
                tax = net_q - base
                ex_tax = base
            else:
                ex_tax = q_money(net_raw, digits=digits)
                tax = q_money(ex_tax * tax_rate / Decimal('100'), digits=digits)
            ex_tax_list.append(ex_tax)
            line_tax_list.append(tax)

        pre_discount_subtotal = sum(ex_tax_list, ZERO)
        disc_val = Decimal(str(order_discount_value or 0))
        if disc_val < ZERO:
            raise ValidationError({'order_discount_value': 'Order discount cannot be negative.'})
        if order_discount_type == 'percentage':
            if disc_val > Decimal('100'):
                raise ValidationError({'order_discount_value': 'Percentage discount cannot exceed 100.'})
            order_discount_amount = q_money(pre_discount_subtotal * disc_val / Decimal('100'), digits=digits)
        else:
            order_discount_amount = q_money(disc_val, digits=digits)
            if order_discount_amount > pre_discount_subtotal and pre_discount_subtotal > ZERO:
                raise ValidationError({'order_discount_value': 'Order discount cannot exceed subtotal.'})
            if pre_discount_subtotal == ZERO:
                order_discount_amount = ZERO

        alloc_discounts = allocate_pro_rata(order_discount_amount, ex_tax_list, digits=digits)
        taxable_weights = [max(ex - d, ZERO) for ex, d in zip(ex_tax_list, alloc_discounts)]
        taxable_subtotal = pre_discount_subtotal - order_discount_amount

        ot_rate = Decimal(str(order_tax_rate or 0))
        if ot_rate < ZERO or ot_rate > Decimal('100'):
            raise ValidationError({'order_tax_rate': 'Order tax rate must be between 0 and 100.'})
        order_tax_amount = q_money(taxable_subtotal * ot_rate / Decimal('100'), digits=digits)
        alloc_taxes = allocate_pro_rata(order_tax_amount, taxable_weights, digits=digits)

        ship = q_money(shipping_cost or 0, digits=digits)
        line_tax_total = sum(line_tax_list, ZERO)
        grand_total = taxable_subtotal + line_tax_total + order_tax_amount + ship

        priced_lines = []
        for ex, ltax, adisc, atax in zip(ex_tax_list, line_tax_list, alloc_discounts, alloc_taxes):
            net_eff = ex - adisc
            ret_total = net_eff + ltax + atax
            priced_lines.append(PricedLine(
                ex_tax=ex,
                line_tax=ltax,
                allocated_order_discount=adisc,
                allocated_order_tax=atax,
                net_effective=net_eff,
                returnable_total=ret_total,
            ))

    return DocumentTotals(
        pre_discount_subtotal=pre_discount_subtotal,
        order_discount_amount=order_discount_amount,
        taxable_subtotal=taxable_subtotal,
        line_tax_total=line_tax_total,
        order_tax_amount=order_tax_amount,
        shipping_cost=ship,
        grand_total=grand_total,
        lines=priced_lines,
    )
