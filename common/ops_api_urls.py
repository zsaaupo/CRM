from django.urls import path

from .ops_api import (
    AuditLogListAPIView, BackupTriggerAPIView, BarcodeSettingListCreateAPIView,
    CustomFieldListCreateAPIView, GatewayCallbackAPIView, GatewayInitiateAPIView,
    LanguageListCreateAPIView, LanguageSwitchAPIView, NotificationListCreateAPIView,
    NotificationReadAPIView, OutboxProcessAPIView, PaymentGatewayListCreateAPIView,
    RestaurantTableListCreateAPIView, SMSTemplateListCreateAPIView, SingletonSettingAPIView,
)


app_name = 'ops_settings'

urlpatterns = [
    path('general/', SingletonSettingAPIView.as_view(setting_key='general'), name='general'),
    path('pos/', SingletonSettingAPIView.as_view(setting_key='pos'), name='pos'),
    path('hrm/', SingletonSettingAPIView.as_view(setting_key='hrm'), name='hrm'),
    path('reward/', SingletonSettingAPIView.as_view(setting_key='reward'), name='reward'),
    path('task/', SingletonSettingAPIView.as_view(setting_key='task'), name='task'),
    path('mail/', SingletonSettingAPIView.as_view(setting_key='mail'), name='mail'),
    path('sms/', SingletonSettingAPIView.as_view(setting_key='sms'), name='sms'),
    path('sms-templates/', SMSTemplateListCreateAPIView.as_view(), name='sms-template-list'),
    path('notifications/', NotificationListCreateAPIView.as_view(), name='notification-list'),
    path('notifications/<int:pk>/read/', NotificationReadAPIView.as_view(), name='notification-read'),
    path('outbox/process/', OutboxProcessAPIView.as_view(), name='outbox-process'),
    path('custom-fields/', CustomFieldListCreateAPIView.as_view(), name='custom-field-list'),
    path('tables/', RestaurantTableListCreateAPIView.as_view(), name='table-list'),
    path('barcodes/', BarcodeSettingListCreateAPIView.as_view(), name='barcode-list'),
    path('languages/', LanguageListCreateAPIView.as_view(), name='language-list'),
    path('languages/switch/', LanguageSwitchAPIView.as_view(), name='language-switch'),
    path('gateways/', PaymentGatewayListCreateAPIView.as_view(), name='gateway-list'),
    path('gateways/initiate/', GatewayInitiateAPIView.as_view(), name='gateway-initiate'),
    path('gateways/callback/', GatewayCallbackAPIView.as_view(), name='gateway-callback'),
    path('audit-logs/', AuditLogListAPIView.as_view(), name='audit-log-list'),
    path('backup/', BackupTriggerAPIView.as_view(), name='backup'),
]
