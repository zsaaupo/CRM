from django.db import IntegrityError, transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.generics import ListAPIView, ListCreateAPIView, RetrieveUpdateDestroyAPIView, UpdateAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated

from account.permissions import HasRequiredPermissions


class BasePagination(PageNumberPagination):
    page_size = 20


class RecordInUse(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = 'This record is in use and cannot be deleted.'
    default_code = 'record_in_use'


class BaseAPIMixin:
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated, HasRequiredPermissions]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    @property
    def required_permissions(self):
        """Derive action permissions from the concrete view model."""
        opts = self.queryset.model._meta
        return {
            method: (f'{opts.app_label}.{action}_{opts.model_name}',)
            for method, action in [('GET', 'view'), ('POST', 'add'),
                                   ('PUT', 'change'), ('PATCH', 'change'), ('DELETE', 'delete')]
        }

    def save_record(self, serializer, **audit):
        try:
            with transaction.atomic():
                serializer.save(**audit)
        except IntegrityError as exc:
            raise ValidationError({
                'non_field_errors': ['Could not save. Check unique fields and related records, then try again.'],
            }) from exc


class BaseListQuerysetMixin:
    pagination_class = BasePagination
    search_fields = ['name']

    def get_queryset(self):
        queryset = super().get_queryset()
        query = self.request.query_params.get('q', '').strip()
        if query:
            condition = Q()
            for field in self.search_fields:
                condition |= Q(**{f'{field}__icontains': query})
            queryset = queryset.filter(condition)
        return queryset.order_by('name', 'pk')


@method_decorator(never_cache, name='dispatch')
class BaseListAPIView(BaseAPIMixin, BaseListQuerysetMixin, ListAPIView):
    http_method_names = ['get', 'head', 'options']


@method_decorator(never_cache, name='dispatch')
class BaseListCreateAPIView(BaseAPIMixin, BaseListQuerysetMixin, ListCreateAPIView):
    http_method_names = ['get', 'post', 'head', 'options']

    def perform_create(self, serializer):
        actor = self.request.user.get_username()
        self.save_record(serializer, create_by=actor, modified_by=actor)

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        response.data = {
            'message': f'{self.queryset.model._meta.verbose_name.capitalize()} created successfully.',
            self.response_key: response.data,
        }
        return response


class BaseUpdateMixin:
    def perform_update(self, serializer):
        self.save_record(serializer, modified_by=self.request.user.get_username())


@method_decorator(never_cache, name='dispatch')
class BaseUpdateAPIView(BaseUpdateMixin, BaseAPIMixin, UpdateAPIView):
    http_method_names = ['put', 'patch', 'options']

    @property
    def required_permissions(self):
        opts = self.queryset.model._meta
        return (f'{opts.app_label}.change_{opts.model_name}',)


@method_decorator(never_cache, name='dispatch')
class BaseDetailAPIView(BaseUpdateMixin, BaseAPIMixin, RetrieveUpdateDestroyAPIView):
    http_method_names = ['get', 'put', 'patch', 'delete', 'head', 'options']
    protected_codes = ()

    def perform_destroy(self, instance):
        if getattr(instance, 'code', None) in self.protected_codes:
            raise RecordInUse('The default lookup record cannot be deleted.')
        try:
            with transaction.atomic():
                instance.delete()
        except ProtectedError as exc:
            raise RecordInUse() from exc
