from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Sum

from warehouse.models import Stock, StockMovement


class Command(BaseCommand):
    help = 'Verify Stock.quantity - Stock.oversold_quantity matches sum of StockMovement.quantity_delta.'

    def handle(self, *args, **options):
        mismatches = 0
        for stock in Stock.objects.select_related('warehouse', 'product').order_by('warehouse_id', 'product_id').iterator():
            ledger_sum = StockMovement.objects.filter(
                warehouse_id=stock.warehouse_id, product_id=stock.product_id,
            ).aggregate(total=Sum('quantity_delta'))['total'] or Decimal('0')
            net_stock = stock.quantity - stock.oversold_quantity
            drift = net_stock - ledger_sum
            if drift != Decimal('0'):
                mismatches += 1
                self.stdout.write(self.style.ERROR(
                    f'Drift warehouse={stock.warehouse_id} product={stock.product_id}: '
                    f'stock={net_stock} ledger={ledger_sum} drift={drift}'
                ))
        if mismatches == 0:
            self.stdout.write(self.style.SUCCESS('Stock ledger reconciled: 0 mismatches.'))
        else:
            raise SystemExit(f'Found {mismatches} stock ledger mismatches.')
