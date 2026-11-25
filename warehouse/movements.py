from decimal import ROUND_HALF_UP, Decimal, localcontext

from .models import StockMovement


def record_lines(warehouse_id, document, lines, kind, business_date, actor, *, original_line=None):
    """Journal the posting inside the caller's balance-update transaction."""
    negative = kind in (StockMovement.Kind.ISSUE, StockMovement.Kind.PURCHASE_RETURN, StockMovement.Kind.TRANSFER_OUT)
    movements = []
    for line in lines:
        item = getattr(line, original_line) if original_line else line
        cost = None
        if kind == StockMovement.Kind.RECEIPT:
            with localcontext() as context:
                context.prec = 50
                cost = (item.net_amount * document.exchange_rate / item.quantity).quantize(
                    Decimal('0.00000001'), rounding=ROUND_HALF_UP)
        movements.append(StockMovement(
            warehouse_id=warehouse_id, product_id=item.product_id, unit_id=item.unit_id,
            kind=kind, quantity=-line.quantity if negative else line.quantity, unit_cost_base=cost,
            source_id=document.pk, source_line_id=line.pk, posting_key=f'{kind}/{document.pk}/{line.pk}',
            business_date=business_date, posted_by=actor.get_username(),
        ))
    StockMovement.objects.bulk_create(movements)
