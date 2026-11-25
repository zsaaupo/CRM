from django.urls import path
from .summaries import FinancialSummaryView
from .party_statements import PartyStatementAPI
from purchase.models import PurchaseOrder
from .settlement_views import OrderSettlementAPI
from .book_reports import DaybookView

from .api_views import (
    AccountStatementAPIView, AccountTransferListCreateAPIView, CashFlowListAPIView, ExpenseCategoryDetailAPIView,
    ExpenseCategoryListCreateAPIView, ExpenseCompleteAPIView, ExpenseDetailAPIView, ExpenseListCreateAPIView,
    ExpenseOptionsAPIView, ExpensePaymentListAPIView, FinancialAccountDetailAPIView, FinancialAccountListCreateAPIView,
    IncomeCategoryDetailAPIView, IncomeCategoryListCreateAPIView, IncomeCompleteAPIView, IncomeDetailAPIView,
    IncomeListCreateAPIView, IncomeOptionsAPIView, IncomePaymentListAPIView, LedgerAPIView, SalesPaymentListAPIView,
)


app_name = 'accounting'

urlpatterns = [
    path('reports/account-balances/', FinancialSummaryView.as_view(as_json=True), name='balance-overview'),
    path('reports/income-expense/', FinancialSummaryView.as_view(kind='categories', as_json=True), name='income-expense'),
    path('customers/<int:pk>/statement/', PartyStatementAPI.as_view(), name='customer-statement'),
    path('suppliers/<int:pk>/statement/', PartyStatementAPI.as_view(party_role='supplier'), name='supplier-statement'),
    path('sales-orders/<int:pk>/settlement/', OrderSettlementAPI.as_view(), name='sale-settlement'),
    path('purchase-orders/<int:pk>/settlement/', OrderSettlementAPI.as_view(order_model=PurchaseOrder), name='purchase-settlement'),
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
    path('account-transfers/', AccountTransferListCreateAPIView.as_view(), name='account-transfer-list'),
    path('expenses/<int:expense_pk>/payments/', ExpensePaymentListAPIView.as_view(), name='expense-payment-list'),
    path('sales-orders/<int:sales_order_pk>/payments/', SalesPaymentListAPIView.as_view(),
         name='sales-payment-list'),
]
