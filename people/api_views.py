from django.contrib.auth import get_user_model
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from common.io_utils import ExportListMixin
from common.view import BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView, BaseUpdateAPIView
from warehouse.scope import all_warehouse_access, scoped_queryset, warehouse_scope

from .models import Biller, CustomerGroup, Lead, Party
from .serializers import (
    BillerSerializer, CustomerGroupSerializer, LeadSerializer, PartyFilterSerializer, PartySerializer,
)
from .services import create_customer_user


class LeadListCreateAPIView(BaseListCreateAPIView):
    serializer_class = LeadSerializer
    queryset = Lead.objects.select_related('assigned_to')
    search_fields = ['name', 'company', 'job_title', 'email', 'phone', 'source', 'assigned_to__username']
    response_key = 'lead'


class LeadDetailAPIView(BaseDetailAPIView):
    serializer_class = LeadSerializer
    queryset = Lead.objects.select_related('assigned_to')
    http_method_names = ['get', 'put', 'patch', 'head', 'options']


@method_decorator(never_cache, name='dispatch')
class LeadOptionsAPIView(BaseAPIMixin, APIView):
    required_permissions = ('people.view_lead', 'people.add_lead')
    http_method_names = ['get', 'head', 'options']

    def get(self, request, pk=None):
        current_assignee = None
        if pk is not None:
            current_assignee = get_object_or_404(Lead, pk=pk).assigned_to_id
        users = get_user_model().objects.filter(Q(is_active=True) | Q(pk=current_assignee)).order_by('username')
        return Response({
            'status': [{'value': value, 'label': label} for value, label in Lead.Status.choices],
            'assigned_to': [
                {'value': user.pk, 'label': user.get_username() if user.is_active else f'{user.get_username()} (inactive)'}
                for user in users
            ],
        })


class LeadEditOptionsAPIView(LeadOptionsAPIView):
    required_permissions = ('people.view_lead', 'people.change_lead')


class PartyQuerysetMixin:
    search_fields = ['code', 'name', 'contact_person', 'email', 'phone', 'alternate_phone', 'tax_number',
                     'address', 'city', 'state', 'postal_code', 'country']

    def get_queryset(self):
        queryset = super().get_queryset()
        # Query parameters are filters, not HTML checkboxes: omitted booleans stay omitted.
        filters = PartyFilterSerializer(data=self.request.query_params.dict())
        filters.is_valid(raise_exception=True)
        return queryset.filter(**filters.validated_data)


class PartyListCreateAPIView(ExportListMixin, PartyQuerysetMixin, BaseListCreateAPIView):
    serializer_class = PartySerializer
    queryset = Party.objects.all()
    response_key = 'party'
    export_filename = 'parties'
    export_title = 'Parties'
    export_columns = [
        ('Code', 'code'), ('Name', 'name'), ('Customer', 'is_customer'),
        ('Supplier', 'is_supplier'), ('Phone', 'phone'), ('Email', 'email'),
    ]


class PartyListAPIView(PartyQuerysetMixin, BaseListAPIView):
    serializer_class = PartySerializer
    queryset = Party.objects.all()


class PartyDetailAPIView(PartyQuerysetMixin, BaseDetailAPIView):
    serializer_class = PartySerializer
    queryset = Party.objects.all()

    def perform_destroy(self, instance):
        instance.delete(actor=self.request.user.get_username())


class PartyUpdateAPIView(PartyQuerysetMixin, BaseUpdateAPIView):
    serializer_class = PartySerializer
    queryset = Party.objects.all()


class CustomerGroupListCreateAPIView(BaseListCreateAPIView):
    serializer_class = CustomerGroupSerializer
    queryset = CustomerGroup.objects.all()
    response_key = 'customer_group'
    search_fields = ['name']
    required_permissions = {
        'GET': ('people.view_customergroup',),
        'POST': ('people.manage_customer_group',),
    }


class CustomerGroupDetailAPIView(BaseDetailAPIView):
    serializer_class = CustomerGroupSerializer
    queryset = CustomerGroup.objects.all()
    required_permissions = {
        'GET': ('people.view_customergroup',),
        'PUT': ('people.manage_customer_group',),
        'PATCH': ('people.manage_customer_group',),
        'DELETE': ('people.manage_customer_group',),
    }


class BillerListCreateAPIView(BaseListCreateAPIView):
    serializer_class = BillerSerializer
    queryset = Biller.objects.prefetch_related('warehouse_links')
    response_key = 'biller'
    search_fields = ['name', 'company_name', 'phone', 'email']
    required_permissions = {
        'GET': ('people.view_biller',),
        'POST': ('people.manage_biller',),
    }


class BillerDetailAPIView(BaseDetailAPIView):
    serializer_class = BillerSerializer
    queryset = Biller.objects.prefetch_related('warehouse_links')
    required_permissions = {
        'GET': ('people.view_biller',),
        'PUT': ('people.manage_biller',),
        'PATCH': ('people.manage_biller',),
        'DELETE': ('people.manage_biller',),
    }

    def perform_destroy(self, instance):
        instance.delete(actor=self.request.user.get_username())


@method_decorator(never_cache, name='dispatch')
class BillerOptionsAPIView(BaseAPIMixin, APIView):
    required_permissions = ('people.view_biller',)
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        scope = warehouse_scope(request.user)
        qs = Biller.objects.filter(is_active=True).distinct()
        wh_param = request.query_params.get('warehouse_id')
        if not all_warehouse_access(scope):
            qs = qs.filter(warehouse_links__warehouse_id=scope.warehouse_id)
            if wh_param and str(wh_param) != str(scope.warehouse_id):
                qs = qs.none()
        elif wh_param:
            qs = qs.filter(warehouse_links__warehouse_id=wh_param)
        return Response({
            'results': [{'value': b.pk, 'label': b.name, 'company_name': b.company_name} for b in qs.order_by('name', 'pk')],
        })



class CustomerPointLedgerListAPIView(BaseAPIMixin, APIView):
    required_permissions = ('people.view_party',)
    http_method_names = ['get', 'head', 'options']

    def get(self, request, customer_id):
        from .models import CustomerPointLedger
        cust = get_object_or_404(Party, pk=customer_id, is_customer=True)
        entries = CustomerPointLedger.objects.filter(customer=cust).order_by('-pk')
        return Response({
            'customer_id': cust.pk,
            'reward_points': cust.reward_points,
            'results': [
                {
                    'id': e.pk,
                    'points_delta': e.points_delta,
                    'reason': e.reason,
                    'sales_order': e.sales_order_id,
                    'expires_at': str(e.expires_at) if e.expires_at else None,
                    'created_at': e.created_at.isoformat(),
                }
                for e in entries
            ],
        })


class CustomerCreateLoginAPIView(BaseAPIMixin, APIView):
    required_permissions = ('people.create_customer_login',)
    http_method_names = ['post', 'options']

    def post(self, request, pk):
        party, created = create_customer_user(
            request.user, pk,
            username=request.data.get('username'),
            password=request.data.get('password'),
        )
        return Response({
            'party_id': party.pk,
            'user_id': party.user_id,
            'username': party.user.username,
            'created': created,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
