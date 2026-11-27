from django.db import transaction
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from common.view import BaseAPIMixin, BaseListAPIView
from warehouse.scope import WarehouseScopeAPIMixin, lock_warehouse_scope

from .models import PackingSlip
from .permissions import PACKING_SLIP_GENERATE, PACKING_SLIP_VIEW
from .serializers import PackingSlipSerializer
from .services import lock_chalan


class PackingSlipListAPIView(WarehouseScopeAPIMixin, BaseListAPIView):
    required_permissions = PACKING_SLIP_VIEW
    warehouse_lookup = 'chalan__warehouse_id'
    queryset = PackingSlip.objects.select_related('chalan__customer', 'chalan__warehouse')
    serializer_class = PackingSlipSerializer
    search_fields = ['slip_number', 'chalan__chalan_number', 'chalan__customer__name', 'chalan__customer__code']
    ordering = ['-created_at', '-pk']


@method_decorator(never_cache, name='dispatch')
class PackingSlipGenerateAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = PACKING_SLIP_GENERATE
    http_method_names = ['post', 'options']

    @transaction.atomic
    def post(self, request, pk):
        scope = lock_warehouse_scope(request.user)
        self.check_permissions(request)
        chalan = lock_chalan(scope, pk=pk)
        if not chalan.items.exists():
            raise ValidationError({'detail': 'Add at least one item to the Chalan before generating a packing slip.'})
        actor = request.user.get_username()
        slip, created = PackingSlip.objects.get_or_create(chalan=chalan, defaults={'create_by': actor, 'modified_by': actor})
        return Response({'packing_slip': PackingSlipSerializer(slip).data, 'created': created},
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
