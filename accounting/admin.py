from django.contrib import admin

from common.admin import AuditedAdmin
from warehouse.admin import WarehouseScopedAdmin

from .models import Expense, ExpenseCategory


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(AuditedAdmin):
    list_display = ['name', 'is_active']
    search_fields = ['name']
    list_filter = ['is_active']


@admin.register(Expense)
class ExpenseAdmin(WarehouseScopedAdmin):
    list_display = ['date', 'category', 'warehouse', 'amount', 'create_by']
    list_filter = ['date', ('category', admin.RelatedOnlyFieldListFilter),
                   ('warehouse', admin.RelatedOnlyFieldListFilter)]
    list_select_related = ['category', 'warehouse']
    search_fields = ['category__name', 'warehouse__name']
