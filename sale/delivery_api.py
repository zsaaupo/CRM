from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from common.view import BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView
from warehouse.scope import WarehouseScopeAPIMixin, lock_warehouse_scope, scoped_queryset

from .delivery import assign_delivery, transition_delivery, update_delivery
from .models import Courier, Delivery, DeliveryStatus
from .permissions import DELIVERY_ASSIGN, DELIVERY_CHANGE, DELIVERY_VIEW
from .serializers import (CourierSerializer, DeliveryAssignmentSerializer, DeliveryDetailSerializer,
                          DeliverySerializer, DeliveryTransitionSerializer)
from .services import lock_chalan


class CourierListCreateAPIView(BaseListCreateAPIView):
    queryset = Courier.objects.all()
    serializer_class = CourierSerializer
    response_key = 'courier'
    role_permissions_only = True
    search_fields = ['name', 'contact_person', 'phone', 'email']


class CourierDetailAPIView(BaseDetailAPIView):
    queryset = Courier.objects.all()
    serializer_class = CourierSerializer
    role_permissions_only = True
    http_method_names = ['get', 'put', 'patch', 'head', 'options']


class DeliveryListAPIView(WarehouseScopeAPIMixin, BaseListAPIView):
    required_permissions = DELIVERY_VIEW
    warehouse_lookup = 'chalan__warehouse_id'
    queryset = Delivery.objects.select_related('chalan__customer', 'chalan__warehouse', 'courier')
    serializer_class = DeliverySerializer
    search_fields = ['chalan__chalan_number', 'chalan__customer__name', 'courier__name', 'tracking_number']
    ordering = ['-updated_at', '-pk']

    def get_queryset(self):
        queryset = super().get_queryset()
        state = self.request.query_params.get('status', '')
        if state and state not in DeliveryStatus.values:
            raise ValidationError({'status': 'Choose a valid delivery status.'})
        return queryset.filter(status=state) if state else queryset


@method_decorator(never_cache, name='dispatch')
class DeliveryActionAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    def lock_scope(self, request):
        scope = lock_warehouse_scope(request.user)
        self.check_permissions(request)
        return scope

    def lock_delivery(self, request, pk):
        scope = self.lock_scope(request)
        chalan = lock_chalan(scope, delivery__pk=pk)
        return get_object_or_404(Delivery.objects.select_for_update(), chalan=chalan, pk=pk), scope


class DeliveryAssignmentAPIView(DeliveryActionAPIView):
    required_permissions = DELIVERY_ASSIGN
    http_method_names = ['post', 'options']

    @transaction.atomic
    def post(self, request, pk):
        chalan = lock_chalan(self.lock_scope(request), pk=pk)
        serializer = DeliveryAssignmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record, created = assign_delivery(chalan, serializer.validated_data, request.user.get_username())
        return Response({'delivery': DeliverySerializer(record).data, 'created': created},
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class DeliveryDetailAPIView(DeliveryActionAPIView):
    required_permissions = {'GET': DELIVERY_VIEW, 'PATCH': DELIVERY_CHANGE}
    http_method_names = ['get', 'patch', 'head', 'options']

    def get(self, request, pk):
        records = DeliveryListAPIView.queryset.select_related('chalan__packing_slip', 'chalan__delivery_payment__account')
        records = records.prefetch_related('status_updates')
        record = get_object_or_404(scoped_queryset(records, request.warehouse_scope, 'chalan__warehouse_id'), pk=pk)
        return Response(DeliveryDetailSerializer(record, context={'request': request}).data)

    @transaction.atomic
    def patch(self, request, pk):
        record, _ = self.lock_delivery(request, pk)
        serializer = DeliveryAssignmentSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        record = update_delivery(record, serializer.validated_data, request.user.get_username())
        return Response(DeliverySerializer(record).data)


class DeliveryTransitionAPIView(DeliveryActionAPIView):
    required_permissions = DELIVERY_CHANGE
    http_method_names = ['post', 'options']

    @transaction.atomic
    def post(self, request, pk):
        record, scope = self.lock_delivery(request, pk)
        serializer = DeliveryTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        changed = transition_delivery(record, serializer.validated_data, request.user, scope)
        return Response({'delivery': DeliverySerializer(record).data, 'changed': changed})
