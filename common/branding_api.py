from io import BytesIO

from django.http import FileResponse, Http404
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe
from rest_framework import serializers
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .branding import (CHANGE_LOGO, VIEW_LOGO, current_logo_revision, logo_png, logo_url, normalize_logo, set_logo,
                       sidebar_logo_png)
from .view import BaseAPIMixin


class LogoUploadSerializer(serializers.Serializer):
    logo = serializers.FileField()

    def validate_logo(self, upload):
        return normalize_logo(upload)


def logo_response(revision):
    return Response({'logo_url': logo_url(revision), 'sidebar_logo_url': logo_url(revision, sidebar=True),
                     'is_custom': revision is not None})


@method_decorator(never_cache, name='dispatch')
class LogoSettingsAPIView(BaseAPIMixin, APIView):
    required_permissions = {'GET': (VIEW_LOGO,), 'PATCH': CHANGE_LOGO, 'DELETE': CHANGE_LOGO}
    parser_classes = [MultiPartParser]
    http_method_names = ['get', 'head', 'patch', 'delete', 'options']

    def get(self, request):
        return logo_response(current_logo_revision())

    def patch(self, request):
        serializer = LogoUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return logo_response(set_logo(request.user, serializer.validated_data['logo']))

    def delete(self, request):
        return logo_response(set_logo(request.user))


@require_safe
def logo_image(request, revision, sidebar=False):
    image = logo_png(revision)
    if not image:
        raise Http404('Logo image not found.')
    if sidebar:
        image = sidebar_logo_png(image)
    return FileResponse(BytesIO(image), content_type='image/png', headers={
        'Cache-Control': 'public, max-age=31536000, immutable', 'X-Content-Type-Options': 'nosniff',
    })
