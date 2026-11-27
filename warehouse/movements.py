from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal, localcontext
from rest_framework.exceptions import ValidationError

from .models import Stock, StockMovement

COST_PRECISION = Decimal('0.00000001')


def post_valued_movements(warehouse_id, movements):
    """Called after updating locked stock quantities, within the same posting transaction."""
    grouped = defaultdict(list)
    for entry in movements:
        grouped[entry.product_id].append(entry)
    with localcontext() as context:
        context.prec = 60
        balances = Stock.objects.select_for_update().filter(warehouse_id=warehouse_id, product_id__in=grouped)
        for stock in balances.order_by('product_id'):
            entries = grouped[stock.product_id]
            old_quantity = stock.quantity - sum(entry.quantity for entry in entries)
            average = stock.average_cost_base
            for entry in entries:
                if entry.quantity < 0 or entry.kind == StockMovement.Kind.COUNT:
                    entry.unit_cost_base = average
            if stock.quantity == 0:
                new_average = None
            elif entries[0].quantity < 0:
                new_average = average
            elif (old_quantity and average is None) or any(entry.unit_cost_base is None for entry in entries):
                new_average = None
            else:
                added_value = sum(entry.quantity * entry.unit_cost_base for entry in entries)
                previous_value = old_quantity * (average or 0)
                new_average = ((previous_value + added_value) / stock.quantity).quantize(COST_PRECISION, rounding=ROUND_HALF_UP)
            if new_average != average:
                stock.average_cost_base = new_average
                stock.save(update_fields=['average_cost_base'])
                stock.refresh_from_db(fields=['average_cost_base'])
                if stock.average_cost_base != new_average:
                    raise ValidationError({'cost': 'The average cost exceeds database precision.'})
    StockMovement.objects.bulk_create(movements)


def record_lines(warehouse_id, document, lines, kind, business_date, actor, *, original_line=None):
    """Journal the posting inside the caller's balance-update transaction."""
    negative = kind in (StockMovement.Kind.ISSUE, StockMovement.Kind.PURCHASE_RETURN, StockMovement.Kind.TRANSFER_OUT)
    lines = list(lines)
    source_costs = {}
    if kind == StockMovement.Kind.SALE_RETURN:
        source_costs = dict(StockMovement.objects.filter(kind=StockMovement.Kind.ISSUE,
            source_id=document.sales_order_id, source_line_id__in=[line.sales_order_item_id for line in lines]
        ).values_list('source_line_id', 'unit_cost_base'))
    elif kind == StockMovement.Kind.TRANSFER_IN:
        source_costs = dict(StockMovement.objects.filter(kind=StockMovement.Kind.TRANSFER_OUT,
            source_id=document.pk).values_list('source_line_id', 'unit_cost_base'))
    movements = []
    for line in lines:
        item = getattr(line, original_line) if original_line else line
        cost = source_costs.get(item.pk)
        if kind == StockMovement.Kind.RECEIPT:
            with localcontext() as context:
                context.prec = 50
                cost = (item.net_amount * document.exchange_rate / item.quantity).quantize(
                    COST_PRECISION, rounding=ROUND_HALF_UP)
        movements.append(StockMovement(
            warehouse_id=warehouse_id, product_id=item.product_id, unit_id=item.unit_id,
            kind=kind, quantity=-line.quantity if negative else line.quantity, unit_cost_base=cost,
            source_id=document.pk, source_line_id=line.pk, posting_key=f'{kind}/{document.pk}/{line.pk}',
            business_date=business_date, posted_by=actor.get_username(),
        ))
    post_valued_movements(warehouse_id, movements)
