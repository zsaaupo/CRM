from django.db import transaction
from django.db.models import Exists, F, Q
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from audit_log.service import record_activity
from common.view import BaseAPIMixin, BaseListCreateAPIView

from .models import Department, Employee, Notice
from .permissions import (NOTICE_BOARD_PERMISSIONS, NOTICE_PUBLISH_PERMISSIONS, NOTICE_REMOVE_PERMISSIONS,
                          VIEW_NOTICE_PERMISSION, can_view_notices)


class NoticeTextField(serializers.CharField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail('invalid')
        return super().to_internal_value(data)


class NoticeSerializer(serializers.ModelSerializer):
    title = NoticeTextField(max_length=255)
    message = NoticeTextField()
    department_id = serializers.IntegerField(min_value=1, allow_null=True, default=None)
    audience = serializers.SerializerMethodField()
    created_at_display = serializers.DateTimeField(source='created_at', format='%Y-%m-%d %H:%M:%S', read_only=True)

    class Meta:
        model = Notice
        fields = ['id', 'title', 'message', 'department_id', 'audience', 'created_at', 'created_at_display']

    def get_audience(self, notice):
        return notice.department.name if notice.department_id is not None else 'All employees'

    def create(self, validated_data):
        department_id = validated_data['department_id']
        if department_id is not None:
            departments = Department.objects.filter(pk=department_id, is_active=True)
            # The shared create transaction holds this lock on SQLite and other databases.
            if not departments.update(updated_at=F('updated_at')):
                raise serializers.ValidationError({'department_id': 'Select an active department.'})
        return super().create(validated_data)


class NoticeFeedPermission(BasePermission):
    message = 'Your login must be linked to a current employee to view notices.'

    def has_permission(self, request, view):
        return request.method not in ('GET', 'HEAD', 'OPTIONS') or can_view_notices(request.user)


class NoticeListCreateAPIView(BaseListCreateAPIView):
    queryset = Notice.objects.filter(deleted_at__isnull=True).select_related('department')
    serializer_class = NoticeSerializer
    permission_classes = [*BaseListCreateAPIView.permission_classes, NoticeFeedPermission]
    required_permissions = {'GET': (VIEW_NOTICE_PERMISSION,), 'POST': NOTICE_PUBLISH_PERMISSIONS}
    ordering = Notice._meta.ordering
    search_fields = ['title']
    response_key = 'notice'

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if user.has_perms(NOTICE_BOARD_PERMISSIONS):
            return queryset
        employees = Employee.objects.filter(user_id=user.pk).order_by()
        return queryset.filter(Exists(employees)).filter(
            Q(department_id__isnull=True) | Q(department_id__in=employees.values('department_id'))
        )

    def perform_create(self, serializer):
        user = self.request.user
        actor = user.get_username()
        self.save_record(serializer, created_by=user, create_by=actor, modified_by=actor)


@method_decorator(never_cache, name='dispatch')
class NoticeOptionsAPIView(BaseAPIMixin, APIView):
    required_permissions = NOTICE_PUBLISH_PERMISSIONS
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        departments = Department.objects.filter(is_active=True).values_list('pk', 'name')
        return Response({'departments': [{'value': pk, 'label': name} for pk, name in departments]})


@method_decorator(never_cache, name='dispatch')
class NoticeRemoveAPIView(BaseAPIMixin, APIView):
    required_permissions = NOTICE_REMOVE_PERMISSIONS
    http_method_names = ['delete', 'options']

    @transaction.atomic
    def delete(self, request, pk):
        notices = Notice.objects.filter(pk=pk)
        now = timezone.now()
        if not notices.filter(deleted_at__isnull=True).update(
            deleted_at=now, deleted_by=request.user, updated_at=now, modified_by=request.user.get_username()
        ):
            raise NotFound('Notice not found.')
        record_activity(notices.get(), 'deleted', actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
