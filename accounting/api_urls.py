from django.urls import path

from .api_views import (
    AccountStatementAPIView, AccountTransferListCreateAPIView, ExpenseCategoryDetailAPIView,
    ExpenseCategoryListCreateAPIView, ExpenseCompleteAPIView, ExpenseDetailAPIView, ExpenseListCreateAPIView,
    ExpenseOptionsAPIView, ExpensePaymentListAPIView, FinancialAccountDetailAPIView, FinancialAccountListCreateAPIView,
    SalesPaymentListAPIView,
)


app_name = 'accounting'

urlpatterns = [
    path('expense-categories/', ExpenseCategoryListCreateAPIView.as_view(), name='expense-category-list'),
    path('expense-categories/<int:pk>/', ExpenseCategoryDetailAPIView.as_view(), name='expense-category-detail'),
    path('expenses/', ExpenseListCreateAPIView.as_view(), name='expense-list'),
    path('expenses/complete/', ExpenseCompleteAPIView.as_view(), name='expense-complete'),
    path('expenses/options/', ExpenseOptionsAPIView.as_view(), name='expense-options'),
    path('expenses/<int:pk>/options/', ExpenseOptionsAPIView.as_view(edit=True), name='expense-edit-options'),
    path('expenses/<int:pk>/', ExpenseDetailAPIView.as_view(), name='expense-detail'),
    path('financial-accounts/', FinancialAccountListCreateAPIView.as_view(), name='financial-account-list'),
    path('financial-accounts/<int:pk>/', FinancialAccountDetailAPIView.as_view(), name='financial-account-detail'),
    path('financial-accounts/<int:pk>/statement/', AccountStatementAPIView.as_view(), name='account-statement'),
    path('account-transfers/', AccountTransferListCreateAPIView.as_view(), name='account-transfer-list'),
    path('expenses/<int:expense_pk>/payments/', ExpensePaymentListAPIView.as_view(), name='expense-payment-list'),
    path('sales-orders/<int:sales_order_pk>/payments/', SalesPaymentListAPIView.as_view(),
         name='sales-payment-list'),
]
