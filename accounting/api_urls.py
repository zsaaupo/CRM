from django.urls import path
from .book_reports import (
    AccountBalancesAPIView, BalanceSheetAPIView, DaybookView, IncomeExpenseReportAPIView,
)

from .api_views import (
    AccountStatementAPIView, AccountTransferListCreateAPIView, CashFlowListAPIView, ClearDueConfirmAPIView,
    ClearDuePreviewAPIView, CustomerDepositListCreateAPIView, ExpenseCategoryDetailAPIView,
    ExpenseCategoryListCreateAPIView, ExpenseCompleteAPIView,
    ExpenseDetailAPIView, ExpenseListCreateAPIView, ExpenseOptionsAPIView, ExpensePaymentListAPIView,
    FinancialAccountDetailAPIView, FinancialAccountListCreateAPIView, IncomeCategoryDetailAPIView,
    IncomeCategoryListCreateAPIView, IncomeCompleteAPIView, IncomeDetailAPIView, IncomeListCreateAPIView,
    IncomeOptionsAPIView, IncomePaymentListAPIView, LedgerAPIView, PartyDueSummaryAPIView, PaymentReverseAPIView,
    PurchasePaymentListCreateAPIView, SalesPaymentListCreateAPIView,
)


app_name = 'accounting'

urlpatterns = [
    path('daybook/', DaybookView.as_view(json_response=True), name='daybook'),
    path('expense-categories/', ExpenseCategoryListCreateAPIView.as_view(), name='expense-category-list'),
    path('expense-categories/<int:pk>/', ExpenseCategoryDetailAPIView.as_view(), name='expense-category-detail'),
    path('expenses/', ExpenseListCreateAPIView.as_view(), name='expense-list'),
    path('expenses/complete/', ExpenseCompleteAPIView.as_view(), name='expense-complete'),
    path('expenses/options/', ExpenseOptionsAPIView.as_view(), name='expense-options'),
    path('expenses/<int:pk>/options/', ExpenseOptionsAPIView.as_view(edit=True), name='expense-edit-options'),
    path('expenses/<int:pk>/', ExpenseDetailAPIView.as_view(), name='expense-detail'),
    path('income-categories/', IncomeCategoryListCreateAPIView.as_view(), name='income-category-list'),
    path('income-categories/<int:pk>/', IncomeCategoryDetailAPIView.as_view(), name='income-category-detail'),
    path('incomes/', IncomeListCreateAPIView.as_view(), name='income-list'),
    path('incomes/complete/', IncomeCompleteAPIView.as_view(), name='income-complete'),
    path('incomes/options/', IncomeOptionsAPIView.as_view(), name='income-options'),
    path('incomes/<int:pk>/options/', IncomeOptionsAPIView.as_view(edit=True), name='income-edit-options'),
    path('incomes/<int:pk>/', IncomeDetailAPIView.as_view(), name='income-detail'),
    path('incomes/<int:income_pk>/payments/', IncomePaymentListAPIView.as_view(), name='income-payment-list'),
    path('financial-accounts/', FinancialAccountListCreateAPIView.as_view(), name='financial-account-list'),
    path('financial-accounts/<int:pk>/', FinancialAccountDetailAPIView.as_view(), name='financial-account-detail'),
    path('financial-accounts/<int:pk>/statement/', AccountStatementAPIView.as_view(), name='account-statement'),
    path('cash-flow/', CashFlowListAPIView.as_view(), name='cash-flow-list'),
    path('ledger/', LedgerAPIView.as_view(), name='ledger'),
    path('account-balances/', AccountBalancesAPIView.as_view(), name='account-balances'),
    path('balance-sheet/', BalanceSheetAPIView.as_view(), name='balance-sheet'),
    path('income-expense-report/', IncomeExpenseReportAPIView.as_view(), name='income-expense-report'),
    path('account-transfers/', AccountTransferListCreateAPIView.as_view(), name='account-transfer-list'),
    path('expenses/<int:expense_pk>/payments/', ExpensePaymentListAPIView.as_view(), name='expense-payment-list'),
    path('sales-orders/<int:sales_order_pk>/payments/', SalesPaymentListCreateAPIView.as_view(),
         name='sales-payment-list'),
    path('purchase-orders/<int:purchase_order_pk>/payments/', PurchasePaymentListCreateAPIView.as_view(),
         name='purchase-payment-list'),
    path('payments/<int:pk>/reverse/', PaymentReverseAPIView.as_view(), name='payment-reverse'),
    path('sales-payments/<int:pk>/reverse/', PaymentReverseAPIView.as_view(default_kind='sales'),
         name='sales-payment-reverse'),
    path('purchase-payments/<int:pk>/reverse/', PaymentReverseAPIView.as_view(default_kind='purchase'),
         name='purchase-payment-reverse'),
    path('parties/<int:pk>/due-summary/', PartyDueSummaryAPIView.as_view(), name='party-due-summary'),
    path('parties/<int:pk>/clear-due/preview/', ClearDuePreviewAPIView.as_view(), name='party-clear-due-preview'),
    path('parties/<int:pk>/clear-due/confirm/', ClearDueConfirmAPIView.as_view(), name='party-clear-due-confirm'),
    path('customers/<int:customer_id>/deposits/', CustomerDepositListCreateAPIView.as_view(), name='customer-deposit-list'),
]
