from django.urls import path

from .page_views import (
    ACCOUNTS, CATEGORIES, EXPENSES, AccountingFormPageView, AccountingListPageView, AccountStatementPageView,
    AccountTransferFormPageView, AccountTransferListPageView, ExpensePaymentListPageView,
)


app_name = 'accounting_pages'

urlpatterns = [
    path('expense-categories/', AccountingListPageView.as_view(resource=CATEGORIES), name='expense-category-list'),
    path('expense-categories/add/', AccountingFormPageView.as_view(resource=CATEGORIES), name='add-expense-category'),
    path('expense-categories/<int:pk>/edit/',
         AccountingFormPageView.as_view(resource=CATEGORIES, mode='edit'), name='edit-expense-category'),
    path('expenses/', AccountingListPageView.as_view(resource=EXPENSES, template_name='accounting/expense_list.html'),
         name='expense-list'),
    path('expenses/add/', AccountingFormPageView.as_view(resource=EXPENSES), name='add-expense'),
    path('expenses/<int:pk>/edit/', AccountingFormPageView.as_view(resource=EXPENSES, mode='edit'), name='edit-expense'),
    path('financial-accounts/', AccountingListPageView.as_view(resource=ACCOUNTS), name='financial-account-list'),
    path('financial-accounts/add/', AccountingFormPageView.as_view(resource=ACCOUNTS), name='add-financial-account'),
    path('financial-accounts/<int:pk>/edit/', AccountingFormPageView.as_view(resource=ACCOUNTS, mode='edit'),
         name='edit-financial-account'),
    path('financial-accounts/<int:pk>/statement/', AccountStatementPageView.as_view(), name='account-statement'),
    path('account-transfers/', AccountTransferListPageView.as_view(), name='account-transfer-list'),
    path('account-transfers/add/', AccountTransferFormPageView.as_view(), name='add-account-transfer'),
    path('expenses/<int:expense_pk>/payments/', ExpensePaymentListPageView.as_view(), name='expense-payment-list'),
]
