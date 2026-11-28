from django.urls import path
from .codes import generate_code_api
from .branding_api import LogoSettingsAPIView, logo_image

from .api_views import (
    TradeStatusDetailAPIView, TradeStatusListAPIView, TradeStatusListCreateAPIView, TradeStatusUpdateAPIView,
)


app_name = 'common'

urlpatterns = [
    path('logo/', LogoSettingsAPIView.as_view(), name='logo-settings'),
    path('logo/image/<uuid:revision>/', logo_image, name='logo-image'),
    path('logo/image/<uuid:revision>/sidebar/', logo_image, {'sidebar': True}, name='sidebar-logo-image'),
    path('codes/generate/', generate_code_api, name='generate-code'),
    path('trade-statuses/', TradeStatusListCreateAPIView.as_view(), name='trade-status-list'),
    path('trade-statuses/list/', TradeStatusListAPIView.as_view(), name='trade-status-list-view'),
    path('trade-statuses/<int:pk>/', TradeStatusDetailAPIView.as_view(), name='trade-status-detail'),
    path('trade-statuses/<int:pk>/update/', TradeStatusUpdateAPIView.as_view(), name='trade-status-update'),
]
