from django.urls import path
from .book_reports import DaybookView

from .models import TransferType
from .page_views import (
    ACCOUNTS, CATEGORIES, EXPENSES, AccountingFormPageView, AccountingListPageView, AccountStatementPageView,
    AccountTransferFormPageView, AccountTransferListPageView, CashFlowListPageView, ExpenseDetailPageView, LedgerPageView,
    INCOMES, INCOME_CATEGORIES, MONEY_TRANSFERS,
)


app_name = 'accounting_pages'
money_transfer_pages = {'resource': MONEY_TRANSFERS, 'transfer_type': TransferType.MONEY_TRANSFER}

urlpatterns = [
    path('daybook/', DaybookView.as_view(), name='daybook'),
    path('expense-categories/', AccountingListPageView.as_view(resource=CATEGORIES), name='expense-category-list'),
    path('expense-categories/add/', AccountingFormPageView.as_view(resource=CATEGORIES), name='add-expense-category'),
    path('expense-categories/<int:pk>/edit/',
         AccountingFormPageView.as_view(resource=CATEGORIES, mode='edit'), name='edit-expense-category'),
    path('expenses/', AccountingListPageView.as_view(resource=EXPENSES, template_name='accounting/expense_list.html'),
         name='expense-list'),
    path('expenses/add/', AccountingFormPageView.as_view(resource=EXPENSES), name='add-expense'),
    path('expenses/<int:pk>/', ExpenseDetailPageView.as_view(), name='expense-detail'),
    path('expenses/<int:pk>/edit/', AccountingFormPageView.as_view(resource=EXPENSES, mode='edit'), name='edit-expense'),
    path('income-categories/', AccountingListPageView.as_view(resource=INCOME_CATEGORIES), name='income-category-list'),
    path('income-categories/add/', AccountingFormPageView.as_view(resource=INCOME_CATEGORIES), name='add-income-category'),
    path('income-categories/<int:pk>/edit/',
         AccountingFormPageView.as_view(resource=INCOME_CATEGORIES, mode='edit'), name='edit-income-category'),
    path('incomes/', AccountingListPageView.as_view(resource=INCOMES, template_name='accounting/expense_list.html'),
         name='income-list'),
    path('incomes/add/', AccountingFormPageView.as_view(resource=INCOMES), name='add-income'),
    path('incomes/<int:pk>/', ExpenseDetailPageView.as_view(resource=INCOMES), name='income-detail'),
    path('incomes/<int:pk>/edit/', AccountingFormPageView.as_view(resource=INCOMES, mode='edit'), name='edit-income'),
    path('financial-accounts/', AccountingListPageView.as_view(resource=ACCOUNTS), name='financial-account-list'),
    path('financial-accounts/add/', AccountingFormPageView.as_view(resource=ACCOUNTS), name='add-financial-account'),
    path('financial-accounts/<int:pk>/edit/', AccountingFormPageView.as_view(resource=ACCOUNTS, mode='edit'),
         name='edit-financial-account'),
    path('financial-accounts/<int:pk>/statement/', AccountStatementPageView.as_view(), name='account-statement'),
    path('cash-flow/', CashFlowListPageView.as_view(), name='cash-flow-list'),
    path('ledger/', LedgerPageView.as_view(), name='ledger'),
    path('account-transfers/', AccountTransferListPageView.as_view(), name='account-transfer-list'),
    path('account-transfers/add/', AccountTransferFormPageView.as_view(), name='add-account-transfer'),
    path('money-transfers/', AccountTransferListPageView.as_view(**money_transfer_pages), name='money-transfer-list'),
    path('money-transfers/add/', AccountTransferFormPageView.as_view(**money_transfer_pages), name='add-money-transfer'),
]
