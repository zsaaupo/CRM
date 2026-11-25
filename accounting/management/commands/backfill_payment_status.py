from django.core.management.base import BaseCommand
from django.db import transaction

from accounting.services import refresh_payment_status
from purchase.models import PurchaseOrder
from sale.models import SalesOrder


class Command(BaseCommand):
    help = 'Idempotently backfill payment_status and recognized_total_base on sales and purchase orders.'

    def handle(self, *args, **options):
        updated_sales = 0
        updated_purchases = 0
        with transaction.atomic():
            for order in SalesOrder.objects.all().iterator():
                before = order.payment_status
                bal = refresh_payment_status(order, save=True)
                if bal.payment_status != before:
                    updated_sales += 1
            for order in PurchaseOrder.objects.all().iterator():
                before = order.payment_status
                bal = refresh_payment_status(order, save=True)
                if bal.payment_status != before:
                    updated_purchases += 1
        self.stdout.write(self.style.SUCCESS(
            f'Backfilled payment status: sales={updated_sales}, purchases={updated_purchases}.'
        ))
