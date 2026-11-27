from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from accounting.models import FinancialAccount, PurchasePayment
from accounting.settlement import balance
from inventory.models import Product, UnitOfMeasure
from purchase.models import PurchaseOrder, PurchaseOrderItem, PurchaseReceipt, PurchaseReturn, PurchaseReturnItem
from purchase.returns import complete_return, validate_items
from warehouse.models import Stock, StockMovement, WareHouse


class PartialReceiptTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username='receiving')
        self.client = APIClient()
        self.client.force_login(self.user)
        wh = WareHouse.objects.create(name='Main')
        unit = UnitOfMeasure.objects.create(name='Pieces', code='DELIVERY')
        product = Product.objects.create(name='Part', sku='DELIVERY', unit=unit, currency_id='BDT')
        self.order = PurchaseOrder.objects.create(currency_id='BDT', exchange_rate=1, warehouse=wh,
            shipping_cost=5, settlement_mode='deferred')
        self.item = PurchaseOrderItem.objects.create(purchase_order=self.order, product=product, unit=unit,
                                                    line_number=1, quantity=10, unit_price=10)
        self.url = reverse('purchase:receipts', args=[self.order.pk])
        self.invoice_url = reverse('purchase:receipt-invoice', args=[self.order.pk])

    def receipt(self, quantity):
        return {'request_key': str(uuid4()), 'lines': [{'line': self.item.pk, 'quantity': quantity}]}

    def invoice(self, receipt):
        return self.client.post(self.invoice_url, {'receipt': receipt, 'invoice_reference': 'INV-10'}, format='json')

    def test_partial_receipt_accrual_invoice_and_final_delivery(self):
        data = self.receipt('4')
        first = self.client.post(self.url, data, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(self.client.post(self.url, data, format='json').status_code, 200)
        self.assertEqual(self.client.post(self.url, self.receipt('7'), format='json').status_code, 400)
        self.order.refresh_from_db()
        self.assertFalse(self.order.is_received)
        self.assertEqual(Stock.objects.get().quantity, 4)
        with self.assertRaises(ValidationError):
            validate_items(self.order, {self.item.pk: Decimal('5')})
        amounts = balance(self.order)
        self.assertEqual((amounts['due'], amounts['uninvoiced_accrual']), (0, 40))
        self.assertEqual(self.invoice(first.data['id']).status_code, 201)
        self.assertEqual(self.invoice(first.data['id']).status_code, 200)
        self.assertEqual(balance(self.order)['due'], 40)
        result = self.client.post(reverse('purchase:purchase-order-receive'),
                                  {'purchase_id': self.order.pk, 'is_received': True}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(Stock.objects.get().quantity, 10)
        self.assertEqual(Stock.objects.get().average_cost_base, 10)
        self.assertEqual(StockMovement.objects.count(), 2)
        last = PurchaseReceipt.objects.last()
        self.assertEqual(last.amount_base, 65)
        self.assertEqual(self.invoice(last.pk).status_code, 201)
        self.assertEqual(balance(self.order)['due'], 105)
        self.assertFalse(PurchasePayment.objects.exists())
        self.assertContains(self.client.get(reverse('purchase_pages:receipts', args=[self.order.pk])), 'INV-10')

    def test_automatic_payment_only_at_invoice_and_return_before_matching(self):
        account = FinancialAccount.objects.create(name='Cash', type='cash', opening_balance=1000)
        self.order.payment_account = account
        self.order.settlement_mode = 'auto_full'
        self.order.save()
        receipt = self.client.post(self.url, self.receipt(4), format='json')
        self.assertFalse(PurchasePayment.objects.exists())
        self.order.refresh_from_db()
        record = PurchaseReturn.objects.create(purchase_order=self.order)
        PurchaseReturnItem.objects.create(purchase_return=record, purchase_order_item=self.item, quantity=1)
        complete_return(self.user, record.pk)
        self.assertEqual(balance(self.order)['uninvoiced_accrual'], 30)
        self.assertEqual(self.invoice(receipt.data['id']).status_code, 201)
        self.assertEqual(self.invoice(receipt.data['id']).status_code, 200)
        self.assertEqual(PurchasePayment.objects.get().amount, 30)
        self.assertEqual(balance(self.order)['due'], 0)
