from io import BytesIO

from django.http import FileResponse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.views import APIView

from common.view import BaseAPIMixin
from .catalog import REPORTS
from .rendering import render_pdf
from .services import snapshot


class PDFRenderer(BaseRenderer):
    media_type = 'application/pdf'
    format = 'pdf'
    charset = None

    def render(self, data, accepted_media_type=None, renderer_context=None):
        # Successful documents use FileResponse; errors stay JSON for PDF-only clients too.
        renderer_context['response']['Content-Type'] = 'application/json'
        return JSONRenderer().render(data, renderer_context=renderer_context)


@method_decorator(never_cache, name='dispatch')
class DocumentPDFAPIView(BaseAPIMixin, APIView):
    role_permissions_only = True
    renderer_classes = [JSONRenderer, PDFRenderer]
    http_method_names = ['get', 'head', 'options']
    kind = ''

    @property
    def required_permissions(self):
        return REPORTS[self.kind].permissions

    def get(self, request, pk):
        document = snapshot(self.kind, pk, request.user)
        response = FileResponse(BytesIO(render_pdf(document)), as_attachment=True,
            filename=document['filename'], content_type='application/pdf')
        response['Cache-Control'] = 'private, no-store, max-age=0'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class PurchasePDFAPIView(DocumentPDFAPIView):
    kind = 'purchase'


class SalePDFAPIView(DocumentPDFAPIView):
    kind = 'sale'


class TransferPDFAPIView(DocumentPDFAPIView):
    kind = 'transfer'
