from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from common.view import BaseAPIMixin, BaseListAPIView

from .leave_balances import ANNUAL, CARRY, BalancePlan, prepare_leave_balances
from .models import LeaveBalance
from .permissions import (ALLOCATE_LEAVE_BALANCES, VIEW_ALL_LEAVE_BALANCES, VIEW_LEAVE_BALANCE,
                          can_view_leave_balances)


class LeaveBalanceFilter(serializers.Serializer):
    as_of = serializers.DateField(default=timezone.localdate)


class LeaveBalanceAllocationSerializer(serializers.Serializer):
    year = serializers.IntegerField(min_value=1, max_value=9999)
    finalize_carry = serializers.BooleanField(default=False)
    reconcile_approved = serializers.BooleanField(default=False)


class LeaveBalanceSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    employee_code = serializers.CharField(source='employee.employee_code', read_only=True)
    leave_type_name = serializers.CharField(source='leave_type.name', read_only=True)
    carry_expires_on = serializers.DateField(read_only=True)
    available_days = serializers.SerializerMethodField()
    reserved_days = serializers.SerializerMethodField()
    carry_status = serializers.SerializerMethodField()

    def get_available_days(self, balance):
        buckets = self.context['plan'].available_buckets(balance, self.context['as_of'])
        if not balance.carry_finalized and self.context['as_of'] <= balance.carry_expires_on:
            return None
        return f'{sum(buckets):.2f}'

    def get_reserved_days(self, balance):
        used = self.context['plan'].used
        return f'{used[balance.pk, ANNUAL] + used[balance.pk, CARRY]:.2f}'

    def get_carry_status(self, balance):
        if not balance.carry_finalized:
            return 'Pending'
        return 'Expired' if self.context['as_of'] > balance.carry_expires_on else 'Available'

    class Meta:
        model = LeaveBalance
        fields = ['id', 'employee_name', 'employee_code', 'leave_type_name', 'year', 'annual_quota_days',
                  'annual_used_days', 'carried_days', 'carry_used_days', 'carry_expires_on', 'carry_status',
                  'reserved_days', 'available_days']


class LeaveBalanceListAPIView(BaseListAPIView):
    queryset = LeaveBalance.objects.select_related('employee', 'leave_type')
    serializer_class = LeaveBalanceSerializer
    required_permissions = (VIEW_LEAVE_BALANCE,)
    ordering = ['employee__full_name', 'employee_id', 'leave_type__name', 'pk']
    search_fields = ['employee__employee_code', 'employee__full_name', 'leave_type__name']

    def list(self, request, *args, **kwargs):
        if not can_view_leave_balances(request.user):
            raise PermissionDenied('Your login must be linked to a current employee to view leave balances.')
        params = LeaveBalanceFilter(data=request.query_params.dict())
        params.is_valid(raise_exception=True)
        day = params.validated_data['as_of']
        queryset = self.filter_queryset(self.get_queryset()).filter(year=day.year)
        if not request.user.has_perms(VIEW_ALL_LEAVE_BALANCES):
            queryset = queryset.filter(employee__user_id=request.user.pk, employee__is_archived=False)
        page = self.paginate_queryset(queryset)
        plan = BalancePlan({row.employee_id for row in page}, {day.year})
        serializer = self.get_serializer(page, many=True, context={'as_of': day, 'plan': plan})
        return self.get_paginated_response(serializer.data)


@method_decorator(never_cache, name='dispatch')
class LeaveBalanceAllocationAPIView(BaseAPIMixin, APIView):
    required_permissions = ALLOCATE_LEAVE_BALANCES
    http_method_names = ['post', 'options']

    def post(self, request):
        serializer = LeaveBalanceAllocationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        actor = request.user.get_username()
        try:
            counts = prepare_leave_balances(values['year'], actor, finalize_carry=values['finalize_carry'],
                                           reconcile=values['reconcile_approved'])
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc
        return Response({'message': 'Leave balance allocation complete.', **counts})
