"""OPS-01..07 API views & serializers for /settings/api/."""

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
import csv
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from account.permissions import has_role_permissions
from common.models import (
    AuditLog, BackupSetting, BarcodeSetting, CustomField, GeneralSetting,
    HRMSetting, Language, MailSetting, Notification, NotificationRecipient,
    OutboxMessage, POSSetting, PaymentGatewayConfig, RestaurantTable,
    RewardSetting, SMSSetting, SMSTemplate, TaskSetting,
)
from common.ops_services import (
    initiate_gateway_payment, process_outbox_messages, publish_notification,
    run_database_backup, update_singleton_setting, verify_and_settle_gateway_callback,
)
from common.view import BaseAPIMixin, BaseListCreateAPIView
from warehouse.scope import all_warehouse_access, warehouse_scope


class GeneralSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = GeneralSetting
        fields = [
            'system_title', 'rtl_layout', 'company_name', 'vat_registration_number',
            'timezone', 'developed_by', 'allow_sale_without_stock', 'enable_packing_slip',
            'enable_in_house_production', 'staff_access', 'costing_method',
            'restore_coupon_on_full_return', 'block_expired_batch_sale',
            'default_currency', 'currency_position', 'decimal_digits', 'invoice_format',
            'date_format', 'expiry_duration_type', 'expiry_value',
        ]


class POSSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = POSSetting
        fields = [
            'default_customer', 'default_biller', 'default_warehouse', 'keyboard_shortcuts',
            'product_grid_count', 'receipt_auto_print', 'receipt_format', 'payment_options',
            'sms_after_sale',
        ]


class HRMSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = HRMSetting
        fields = [
            'default_check_in', 'default_check_out', 'attendance_import_config',
            'payroll_month_policy_freeze', 'carry_expiry_month_day',
        ]


class RewardSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = RewardSetting
        fields = [
            'enable_reward_points', 'sold_amount_per_point',
            'minimum_sold_amount_to_get_point', 'redeem_amount_per_point',
            'point_expiry_duration_days',
        ]


class TaskSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskSetting
        fields = ['block_close_with_open_subtasks']


SINGLETON_SERIALIZERS = {
    'general': (GeneralSetting, GeneralSettingSerializer),
    'pos': (POSSetting, POSSettingSerializer),
    'hrm': (HRMSetting, HRMSettingSerializer),
    'reward': (RewardSetting, RewardSettingSerializer),
    'task': (TaskSetting, TaskSettingSerializer),
}


class SingletonSettingAPIView(BaseAPIMixin, APIView):
    setting_key = 'general'
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'put', 'patch', 'head', 'options']

    def get(self, request):
        model_cls, ser_cls = SINGLETON_SERIALIZERS[self.setting_key]
        obj, _ = model_cls.objects.get_or_create(pk=1)
        return Response(ser_cls(obj).data)

    def patch(self, request):
        if not (request.user.is_superuser or has_role_permissions(request.user, ('common.change_generalsetting',))):
            raise PermissionDenied('Permission to change settings is required.')
        model_cls, ser_cls = SINGLETON_SERIALIZERS[self.setting_key]
        obj, _ = model_cls.objects.get_or_create(pk=1)
        ser = ser_cls(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        updated = update_singleton_setting(request.user, model_cls, ser.validated_data, request=request)
        return Response(ser_cls(updated).data)

    def put(self, request):
        return self.patch(request)


class NotificationListCreateAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'head', 'options']

    def get(self, request):
        qs = NotificationRecipient.objects.filter(user=request.user).select_related('notification').order_by('-pk')
        unread = qs.filter(read_at__isnull=True).count()
        return Response({
            'unread_count': unread,
            'results': [
                {
                    'id': r.notification_id,
                    'title': r.notification.title,
                    'body': r.notification.body,
                    'audience': r.notification.audience,
                    'read_at': r.read_at,
                    'created_at': r.notification.created_at,
                }
                for r in qs[:50]
            ],
        })

    def post(self, request):
        if not (request.user.is_superuser or has_role_permissions(request.user, ('common.add_notification',))):
            raise PermissionDenied('Permission common.add_notification required.')
        n = publish_notification(
            request.user,
            title=request.data.get('title') or '',
            body=request.data.get('body') or '',
            audience=request.data.get('audience') or 'all',
            user_ids=request.data.get('user_ids'),
        )
        return Response({'id': n.pk, 'title': n.title}, status=status.HTTP_201_CREATED)


class NotificationReadAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['post', 'options']

    def post(self, request, pk):
        rec = get_object_or_404(NotificationRecipient, notification_id=pk, user=request.user)
        if rec.read_at is None:
            rec.read_at = timezone.now()
            rec.save(update_fields=['read_at'])
        return Response({'id': pk, 'read_at': rec.read_at})


class OutboxProcessAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['post', 'options']

    def post(self, request):
        if not request.user.is_superuser:
            raise PermissionDenied('Superuser required.')
        return Response(process_outbox_messages())


class CustomFieldSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomField
        fields = [
            'id', 'entity', 'label', 'key', 'field_type', 'default', 'options',
            'required', 'show_in_table', 'show_in_invoice', 'admin_only', 'is_active',
        ]


class CustomFieldListCreateAPIView(BaseListCreateAPIView):
    serializer_class = CustomFieldSerializer
    queryset = CustomField.objects.all()
    response_key = 'custom_field'


class RestaurantTableSerializer(serializers.ModelSerializer):
    class Meta:
        model = RestaurantTable
        fields = ['id', 'name', 'code', 'warehouse', 'capacity', 'is_active']


class RestaurantTableListCreateAPIView(BaseListCreateAPIView):
    serializer_class = RestaurantTableSerializer
    queryset = RestaurantTable.objects.all()
    response_key = 'restaurant_table'


class BarcodeSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = BarcodeSetting
        fields = ['id', 'name', 'paper', 'width_mm', 'height_mm', 'margins', 'rows', 'columns', 'thermal_width', 'is_default']


class BarcodeSettingListCreateAPIView(BaseListCreateAPIView):
    serializer_class = BarcodeSettingSerializer
    queryset = BarcodeSetting.objects.all()
    response_key = 'barcode_setting'


class LanguageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Language
        fields = ['id', 'code', 'name', 'is_rtl', 'is_active']


class LanguageListCreateAPIView(BaseListCreateAPIView):
    serializer_class = LanguageSerializer
    queryset = Language.objects.all()
    response_key = 'language'


class PaymentGatewayConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentGatewayConfig
        fields = ['id', 'provider', 'enabled', 'mode']  # Never serialize credentials_encrypted (G-12)


class PaymentGatewayListCreateAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'head', 'options']

    def get(self, request):
        qs = PaymentGatewayConfig.objects.order_by('provider')
        return Response({'results': PaymentGatewayConfigSerializer(qs, many=True).data})

    def post(self, request):
        if not (request.user.is_superuser or has_role_permissions(request.user, ('common.manage_gateways',))):
            raise PermissionDenied('Permission common.manage_gateways is required.')
        provider = request.data.get('provider')
        if provider not in PaymentGatewayConfig.Provider.values:
            raise ValidationError({'provider': 'Invalid gateway provider.'})
        cfg, _ = PaymentGatewayConfig.objects.update_or_create(
            provider=provider,
            defaults={
                'enabled': bool(request.data.get('enabled', False)),
                'mode': request.data.get('mode') or PaymentGatewayConfig.Mode.SANDBOX,
                'credentials_encrypted': request.data.get('secret') or request.data.get('credentials') or '',
                'modified_by': request.user.get_username(),
            },
        )
        return Response(PaymentGatewayConfigSerializer(cfg).data, status=status.HTTP_201_CREATED)


class GatewayInitiateAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['post', 'options']

    def post(self, request):
        res = initiate_gateway_payment(
            request.user,
            provider=request.data.get('provider') or 'sslcommerz',
            order_id=int(request.data.get('order')),
            amount=request.data.get('amount'),
            account_id=int(request.data['account']) if request.data.get('account') else None,
        )
        return Response(res, status=status.HTTP_201_CREATED)


class GatewayCallbackAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['post', 'options']

    def post(self, request):
        res = verify_and_settle_gateway_callback(
            request.user,
            provider_ref=request.data.get('provider_ref') or '',
            signature=request.data.get('signature') or '',
            payload=request.data,
        )
        return Response(res)


class AuditLogListAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        if not (request.user.is_superuser or has_role_permissions(request.user, ('common.view_auditlog',))):
            raise PermissionDenied('Permission common.view_auditlog required.')
        qs = AuditLog.objects.select_related('actor', 'warehouse').order_by('-created_at', '-pk')
        scope = warehouse_scope(request.user)
        if not all_warehouse_access(scope):
            qs = qs.filter(warehouse_id=scope.warehouse_id)
        for qp, field in [('module', 'module'), ('action', 'action'), ('user', 'actor_id'), ('warehouse', 'warehouse_id')]:
            if request.query_params.get(qp):
                qs = qs.filter(**{field: request.query_params[qp]})
        if request.query_params.get('date_from'):
            qs = qs.filter(created_at__date__gte=request.query_params['date_from'])
        if request.query_params.get('date_to'):
            qs = qs.filter(created_at__date__lte=request.query_params['date_to'])
        if request.query_params.get('search'):
            s = request.query_params['search'].strip()
            qs = qs.filter(Q(object_repr__icontains=s) | Q(model_name__icontains=s))

        if request.query_params.get('export') == 'csv':
            resp = HttpResponse(content_type='text/csv; charset=utf-8')
            resp['Content-Disposition'] = 'attachment; filename="audit-logs.csv"'
            writer = csv.writer(resp)
            writer.writerow(['created_at', 'actor', 'module', 'action', 'model_name', 'object_id', 'warehouse_id'])
            for row in qs[:500]:
                writer.writerow([
                    row.created_at.isoformat(),
                    row.actor.get_username() if row.actor_id else '',
                    row.module, row.action, row.model_name, row.object_id, row.warehouse_id or '',
                ])
            return resp

        return Response({
            'results': [
                {
                    'id': r.pk,
                    'created_at': r.created_at.isoformat(),
                    'actor': r.actor.get_username() if r.actor_id else None,
                    'module': r.module,
                    'action': r.action,
                    'model_name': r.model_name,
                    'object_id': r.object_id,
                    'object_repr': r.object_repr,
                    'warehouse': r.warehouse_id,
                    'changes': r.changes,
                }
                for r in qs[:100]
            ],
        })


class BackupTriggerAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['post', 'options']

    def post(self, request):
        return Response(run_database_backup(request.user), status=status.HTTP_201_CREATED)
