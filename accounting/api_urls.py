from django.urls import path

from .api_views import (
    ExpenseCategoryDetailAPIView, ExpenseCategoryListCreateAPIView, ExpenseDetailAPIView,
    ExpenseListCreateAPIView, ExpenseOptionsAPIView,
)


app_name = 'accounting'

urlpatterns = [
    path('expense-categories/', ExpenseCategoryListCreateAPIView.as_view(), name='expense-category-list'),
    path('expense-categories/<int:pk>/', ExpenseCategoryDetailAPIView.as_view(), name='expense-category-detail'),
    path('expenses/', ExpenseListCreateAPIView.as_view(), name='expense-list'),
    path('expenses/options/', ExpenseOptionsAPIView.as_view(), name='expense-options'),
    path('expenses/<int:pk>/options/', ExpenseOptionsAPIView.as_view(edit=True), name='expense-edit-options'),
    path('expenses/<int:pk>/', ExpenseDetailAPIView.as_view(), name='expense-detail'),
]
