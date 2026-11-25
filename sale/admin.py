from django.contrib import admin

from accounting.admin import PaymentAccountAdminMixin
from warehouse.admin import WarehouseScopedAdmin

from .models import SalesOrder, SalesOrderItem, SalesQuotation, SalesQuotationItem, SalesReturn, SalesReturnItem


@admin.register(SalesOrder)
class SalesOrderAdmin(PaymentAccountAdminMixin, WarehouseScopedAdmin):
    posted_lookup = 'is_completed'
    list_display = ['order_number', 'order_date', 'customer', 'warehouse', 'is_completed']
    list_filter = ['is_completed', 'order_date']
    search_fields = ['order_number', 'customer__name', 'customer_reference']
    list_select_related = ['customer', 'warehouse']


@admin.register(SalesOrderItem)
class SalesOrderItemAdmin(WarehouseScopedAdmin):
    warehouse_lookup = 'sales_order__warehouse_id'
    scoped_foreign_keys = {'sales_order': 'warehouse_id'}
    posted_lookup = 'sales_order__is_completed'
    list_display = ['sales_order', 'line_number', 'product', 'unit', 'quantity', 'unit_price']
    search_fields = ['sales_order__order_number', 'product__name', 'product__sku']
    list_select_related = ['sales_order', 'product', 'unit']


@admin.register(SalesReturn)
class SalesReturnAdmin(WarehouseScopedAdmin):
    warehouse_lookup = 'sales_order__warehouse_id'
    scoped_foreign_keys = {'sales_order': 'warehouse_id'}
    posted_lookup = 'is_completed'
    list_display = ['return_number', 'sales_order', 'return_date', 'is_completed']
    list_filter = ['is_completed', 'return_date']
    search_fields = ['return_number', 'sales_order__order_number']
    list_select_related = ['sales_order']


@admin.register(SalesReturnItem)
class SalesReturnItemAdmin(WarehouseScopedAdmin):
    warehouse_lookup = 'sales_return__sales_order__warehouse_id'
    scoped_foreign_keys = {'sales_return': 'sales_order__warehouse_id', 'sales_order_item': 'sales_order__warehouse_id'}
    posted_lookup = 'sales_return__is_completed'
    list_display = ['sales_return', 'sales_order_item', 'quantity']
    search_fields = ['sales_return__return_number', 'sales_order_item__product__name', 'sales_order_item__product__sku']
    list_select_related = ['sales_return', 'sales_order_item__sales_order']


@admin.register(SalesQuotation)
class SalesQuotationAdmin(WarehouseScopedAdmin):
    exclude = ['issued_document']
    posted_lookup = 'status'
    unposted_value = SalesQuotation.Status.DRAFT
    list_display = ['quotation_number', 'customer', 'warehouse', 'order_date', 'valid_until', 'status']
    list_filter = ['status', 'order_date']
    search_fields = ['quotation_number', 'customer__name', 'customer_reference']
    list_select_related = ['customer', 'warehouse']


@admin.register(SalesQuotationItem)
class SalesQuotationItemAdmin(WarehouseScopedAdmin):
    warehouse_lookup = 'quotation__warehouse_id'
    scoped_foreign_keys = {'quotation': 'warehouse_id'}
    posted_lookup = 'quotation__status'
    unposted_value = SalesQuotation.Status.DRAFT
    list_display = ['quotation', 'line_number', 'product', 'unit', 'quantity', 'unit_price']
    search_fields = ['quotation__quotation_number', 'product__name', 'product__sku']
    list_select_related = ['quotation', 'product', 'unit']



from .models import (
    CashRegisterSession, Challan, ChallanItem, Coupon, CouponRedemption, Courier,
    Delivery, DeliveryItem, Discount, DiscountPlan, GiftCard, GiftCardEntry,
    PackingSlip, PackingSlipItem, PosDraft, QuotationConversion, RegisterMovement,
)


@admin.register(QuotationConversion)
class QuotationConversionAdmin(admin.ModelAdmin):
    list_display = ['quotation_revision', 'kind', 'target_type', 'target_id', 'created_by', 'created_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CashRegisterSession)
class CashRegisterSessionAdmin(admin.ModelAdmin):
    list_display = ['pk', 'user', 'warehouse', 'status', 'opening_cash', 'closing_cash_expected', 'variance', 'opened_at']
    list_filter = ['status', 'warehouse']


@admin.register(RegisterMovement)
class RegisterMovementAdmin(admin.ModelAdmin):
    list_display = ['session', 'kind', 'amount', 'account', 'request_key']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PosDraft)
class PosDraftAdmin(admin.ModelAdmin):
    list_display = ['pk', 'user', 'warehouse', 'customer', 'updated_at']


@admin.register(DiscountPlan)
class DiscountPlanAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_active']


@admin.register(Discount)
class DiscountAdmin(admin.ModelAdmin):
    list_display = ['plan', 'name', 'discount_type', 'value', 'valid_from', 'valid_till', 'is_active']


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ['code', 'coupon_type', 'amount', 'quantity', 'used_count', 'expired_date', 'is_active']


@admin.register(CouponRedemption)
class CouponRedemptionAdmin(admin.ModelAdmin):
    list_display = ['coupon', 'sales_order', 'customer', 'amount']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(GiftCard)
class GiftCardAdmin(admin.ModelAdmin):
    list_display = ['card_no', 'amount', 'balance', 'customer', 'expired_date', 'is_active']


@admin.register(GiftCardEntry)
class GiftCardEntryAdmin(admin.ModelAdmin):
    list_display = ['gift_card', 'kind', 'amount', 'sales_payment', 'request_key']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Courier)
class CourierAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'is_active']


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ['reference_number', 'sales_order', 'courier', 'status', 'created_at']


@admin.register(DeliveryItem)
class DeliveryItemAdmin(admin.ModelAdmin):
    list_display = ['delivery', 'sales_order_item', 'quantity']


@admin.register(PackingSlip)
class PackingSlipAdmin(admin.ModelAdmin):
    list_display = ['reference_number', 'sales_order', 'delivery', 'status']


@admin.register(PackingSlipItem)
class PackingSlipItemAdmin(admin.ModelAdmin):
    list_display = ['packing_slip', 'sales_order_item', 'quantity']


@admin.register(Challan)
class ChallanAdmin(admin.ModelAdmin):
    list_display = ['reference_number', 'courier', 'status', 'closing_date', 'amount']


@admin.register(ChallanItem)
class ChallanItemAdmin(admin.ModelAdmin):
    list_display = ['challan', 'packing_slip', 'sales_order', 'is_open']
