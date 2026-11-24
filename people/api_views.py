from django.contrib.auth import get_user_model
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.response import Response
from rest_framework.views import APIView

from common.view import BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView, BaseUpdateAPIView

from .models import Lead, Party
from .serializers import LeadSerializer, PartyFilterSerializer, PartySerializer


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


class PartyListCreateAPIView(PartyQuerysetMixin, BaseListCreateAPIView):
    serializer_class = PartySerializer
    queryset = Party.objects.all()
    response_key = 'party'


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
