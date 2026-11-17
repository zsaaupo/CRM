"""Document adapters supported by the shared report application."""
from dataclasses import dataclass

from purchase.models import PurchaseOrder
from sale.models import SalesOrder
from warehouse.models import WarehouseTransfer


@dataclass(frozen=True)
class ReportSpec:
    kind: str
    title: str
    model: type
    permissions: tuple[str, ...]
    completed_field: str
    completed_label: str
    pending_label: str
    party_field: str = ''
    expected_field: str = ''


REPORTS = {
    'purchase': ReportSpec('purchase', 'Purchase order', PurchaseOrder,
        ('purchase.view_purchaseorder', 'purchase.view_purchaseorderitem'),
        'is_received', 'Received', 'Not received', 'supplier', 'expected_date'),
    'sale': ReportSpec('sale', 'Sales order', SalesOrder,
        ('sale.view_salesorder', 'sale.view_salesorderitem'),
        'is_completed', 'Completed', 'Not completed', 'customer', 'expected_delivery_date'),
    'transfer': ReportSpec('transfer', 'Warehouse transfer', WarehouseTransfer,
        ('warehouse.view_warehousetransfer',), 'is_completed', 'Completed', 'Pending'),
}
