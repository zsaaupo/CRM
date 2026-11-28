from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, OperationalError
from django.db.models import Case, Exists, OuterRef, Value, When
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import serializers, status
from rest_framework.generics import RetrieveAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from common.view import BaseAPIMixin, BaseListCreateAPIView

from .models import Designation, EmployeeDesignationChange
from .permissions import CORRECT_PROMOTIONS, RECORD_PROMOTIONS, VIEW_PROMOTIONS
from .promotions import (HISTORY, DesignationConflict, business_date, correct_designation_change, eligible_employees,
                         record_designation_change, valid_history_dates, with_previous_designation)


def history_queryset():
    queryset = EmployeeDesignationChange.objects.select_related('recorded_by')
    return with_previous_designation(queryset).annotate(
        is_superseded=Exists(EmployeeDesignationChange.objects.filter(supersedes_id=OuterRef('pk'))),
        date_eligible=Case(When(valid_history_dates(), then=Value(True)), default=Value(False)),
    )


def change_status(entry):
    if entry.kind == HISTORY.OPENING:
        return 'Opening assignment'
    if entry.is_superseded:
        return 'Superseded'
    if entry.kind == HISTORY.CANCEL:
        return 'Cancelled'
    if not entry.date_eligible:
        return 'Skipped outside employment'
    return 'Scheduled' if entry.effective_date > business_date() else 'Effective'


class DesignationHistorySerializer(serializers.ModelSerializer):
    previous_designation_name = serializers.SerializerMethodField()
    recorded_by_name = serializers.CharField(source='recorded_by.get_username', default=None, read_only=True)
    status = serializers.SerializerMethodField()

    def get_previous_designation_name(self, entry):
        if entry.kind == HISTORY.OPENING:
            return ''
        if entry.is_superseded or entry.kind == HISTORY.CANCEL:
            return entry.recorded_previous_designation_name
        return entry.effective_previous_name or ''

    def get_status(self, entry):
        return change_status(entry)

    class Meta:
        model = EmployeeDesignationChange
        fields = ['id', 'employee_id', 'employee_code', 'employee_name', 'designation_id', 'designation_name',
                  'previous_designation_name', 'recorded_previous_designation_name', 'effective_date', 'kind', 'status',
                  'supersedes_id', 'remarks', 'recorded_by_id', 'recorded_by_name', 'created_at']
        read_only_fields = fields


class PromotionInputSerializer(serializers.Serializer):
    employee_id = serializers.IntegerField(min_value=1, max_value=2**63 - 1)
    designation_id = serializers.IntegerField(min_value=1, max_value=2**63 - 1)
    effective_date = serializers.DateField()
    remarks = serializers.CharField(max_length=255, allow_blank=True, default='')


class PromotionCorrectionSerializer(serializers.Serializer):
    designation_id = serializers.IntegerField(min_value=1, max_value=2**63 - 1, allow_null=True)
    remarks = serializers.CharField(max_length=255)


class PromotionFilters(serializers.Serializer):
    employee_id = serializers.IntegerField(min_value=1, max_value=2**63 - 1, required=False)


def save_history(operation, actor, **data):
    try:
        entry = operation(actor=actor, **data)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(exc.message_dict) from exc
    except DesignationConflict as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
    except IntegrityError:
        return Response({'detail': 'Designation history changed. Refresh the page and try again.'},
                        status=status.HTTP_409_CONFLICT)
    except OperationalError as exc:
        if not any(word in str(exc).lower() for word in ('locked', 'deadlock')):
            raise
        return Response({'detail': 'Designation history is busy. Try again shortly.'}, status=status.HTTP_409_CONFLICT)
    return Response({'message': 'Designation history recorded.',
                     'designation_change': DesignationHistorySerializer(history_queryset().get(pk=entry.pk)).data},
                    status=status.HTTP_201_CREATED)


class PromotionListCreateAPIView(BaseListCreateAPIView):
    queryset = history_queryset()
    serializer_class = DesignationHistorySerializer
    required_permissions = {'GET': (VIEW_PROMOTIONS,), 'POST': RECORD_PROMOTIONS}
    ordering = ['-effective_date', '-pk']
    search_fields = ['employee_name', 'employee_code', 'designation_name', 'recorded_previous_designation_name']

    def get_queryset(self):
        filters = PromotionFilters(data=self.request.query_params.dict())
        filters.is_valid(raise_exception=True)
        return super().get_queryset().filter(**filters.validated_data)

    def create(self, request, *args, **kwargs):
        serializer = PromotionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return save_history(record_designation_change, request.user, **serializer.validated_data)


@method_decorator(never_cache, name='dispatch')
class PromotionDetailAPIView(BaseAPIMixin, RetrieveAPIView):
    queryset = history_queryset()
    serializer_class = DesignationHistorySerializer
    required_permissions = (VIEW_PROMOTIONS,)
    http_method_names = ['get', 'head', 'options']


@method_decorator(never_cache, name='dispatch')
class PromotionCorrectionAPIView(BaseAPIMixin, APIView):
    required_permissions = CORRECT_PROMOTIONS
    http_method_names = ['post', 'options']

    def post(self, request, pk):
        target = get_object_or_404(EmployeeDesignationChange, pk=pk)
        serializer = PromotionCorrectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return save_history(correct_designation_change, request.user, target=target, **serializer.validated_data)


@method_decorator(never_cache, name='dispatch')
class CorrectionOptionsAPIView(BaseAPIMixin, APIView):
    required_permissions = CORRECT_PROMOTIONS
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        titles = Designation.objects.filter(is_active=True).values_list('pk', 'name')
        return Response({'designation_id': [{'value': pk, 'label': name} for pk, name in titles]})


class PromotionOptionsAPIView(CorrectionOptionsAPIView):
    required_permissions = RECORD_PROMOTIONS

    def get(self, request):
        response = super().get(request)
        employees = eligible_employees().values_list('pk', 'full_name', 'employee_code')
        response.data['employee_id'] = [{'value': pk, 'label': f'{name} ({code})'} for pk, name, code in employees]
        return response
