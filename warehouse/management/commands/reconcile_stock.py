from decimal import Decimal
from itertools import groupby
from operator import itemgetter

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Value

from warehouse.models import Stock, StockMovement


class Command(BaseCommand):
    help = 'Compare warehouse balances with their signed movement quantities.'

    def add_arguments(self, parser):
        parser.add_argument('--warehouse', type=int)

    def handle(self, *args, warehouse=None, **options):
        stocks = Stock.objects.order_by()
        journal = StockMovement.objects.order_by()
        if warehouse is not None:
            stocks = stocks.filter(warehouse_id=warehouse)
            journal = journal.filter(warehouse_id=warehouse)
        # One read statement keeps the two sides at the same cutoff during concurrent posting.
        fields = ('warehouse_id', 'product_id', 'quantity', 'source_table')
        balances = stocks.annotate(source_table=Value('stock')).values_list(*fields)
        movements = journal.annotate(source_table=Value('movement')).values_list(*fields)
        rows = balances.union(movements, all=True).order_by('warehouse_id', 'product_id')
        checked = differences = 0
        for (branch, product), entries in groupby(rows.iterator(), key=itemgetter(0, 1)):
            checked += 1
            current = None
            total = Decimal('0')
            for _, _, quantity, source in entries:
                if source == 'stock':
                    current = quantity
                else:
                    total += quantity
            if current != total:
                differences += 1
                self.stdout.write(f'Warehouse {branch}, product {product}: stock {current}, movements {total}.')
        if differences:
            raise CommandError(f'{differences} of {checked} warehouse/product balances differ.')
        self.stdout.write(self.style.SUCCESS(f'Reconciled {checked} warehouse/product balances.'))
