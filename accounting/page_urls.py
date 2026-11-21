from django.urls import path

from .page_views import AccountingFormPageView, AccountingListPageView, CATEGORIES, EXPENSES


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
]
