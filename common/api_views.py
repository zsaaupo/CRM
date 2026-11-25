from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from account.permissions import has_role_permissions
from .io_utils import commit_import_job, create_and_validate_import_job, generate_import_template, get_import_spec
from .models import ImportJob, TradeStatus
from .serializers import ImportJobSerializer, TradeStatusSerializer
from .view import BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView, BaseUpdateAPIView


class TradeStatusListCreateAPIView(BaseListCreateAPIView):
    serializer_class = TradeStatusSerializer
    queryset = TradeStatus.objects.all()
    response_key = 'trade_status'
    search_fields = ['name', 'code']


class TradeStatusListAPIView(BaseListAPIView):
    serializer_class = TradeStatusSerializer
    queryset = TradeStatus.objects.all()
    search_fields = ['name', 'code']


class TradeStatusDetailAPIView(BaseDetailAPIView):
    serializer_class = TradeStatusSerializer
    queryset = TradeStatus.objects.all()
    protected_codes = ('draft',)


class TradeStatusUpdateAPIView(BaseUpdateAPIView):
    serializer_class = TradeStatusSerializer
    queryset = TradeStatusDetailAPIView.queryset


class ImportCommitSerializer(serializers.Serializer):
    request_key = serializers.UUIDField()


class ImportTemplateAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'head', 'options']

    def get(self, request, kind):
        spec = get_import_spec(kind)
        if not has_role_permissions(request.user, spec.permissions):
            raise PermissionDenied(f'You do not have permission to import {kind}.')
        return generate_import_template(kind)


class ImportPreviewAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    http_method_names = ['post', 'options']

    def post(self, request, kind):
        upload = request.FILES.get('file')
        if upload is not None:
            filename = upload.name
            file_obj = upload
        elif 'csv' in request.data:
            filename = request.data.get('filename') or f'{kind}.csv'
            file_obj = str(request.data['csv'])
        else:
            raise ValidationError({'file': 'Upload a CSV/XLSX file or pass "csv" content.'})
        context = {k: v for k, v in request.data.items() if k not in ('file', 'csv', 'filename')}
        job = create_and_validate_import_job(request.user, kind, file_obj, filename=filename, context=context, request=request)
        return Response(ImportJobSerializer(job).data, status=status.HTTP_200_OK)


class ImportCommitAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['post', 'options']

    def post(self, request, pk):
        ser = ImportCommitSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        context = {k: v for k, v in request.data.items() if k != 'request_key'}
        job, created = commit_import_job(request.user, pk, ser.validated_data['request_key'], context=context, request=request)
        data = ImportJobSerializer(job).data
        data['idempotent_replay'] = not created
        return Response(data, status=status.HTTP_200_OK)


class ImportJobListAPIView(BaseListAPIView):
    serializer_class = ImportJobSerializer
    queryset = ImportJob.objects.select_related('created_by')
    permission_classes = [IsAuthenticated]
    search_fields = ['kind', 'status']

    def get_queryset(self):
        qs = super().get_queryset()
        if not self.request.user.is_superuser:
            qs = qs.filter(created_by=self.request.user)
        return qs


class ImportJobDetailAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'head', 'options']

    def get(self, request, pk):
        job = get_object_or_404(ImportJob, pk=pk)
        if not request.user.is_superuser and job.created_by_id != request.user.pk:
            raise PermissionDenied('You do not have access to this import job.')
        return Response(ImportJobSerializer(job).data)
