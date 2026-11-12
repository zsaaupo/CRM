from common.view import (
    BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView,
    BaseUpdateAPIView,
)

from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.generics import CreateAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from inventory.models import Product
from .models import Stock, WareHouse
from .serializers import StockSerializer, WareHouseSerializer


class WareHouseListCreateAPIView(BaseListCreateAPIView):
    serializer_class = WareHouseSerializer
    queryset = WareHouse.objects.all()
    response_key = 'warehouse'
    search_fields = ['name', 'code', 'address', 'phone', 'email']


class WareHouseListAPIView(BaseListAPIView):
    serializer_class = WareHouseSerializer
    queryset = WareHouse.objects.all()
    search_fields = WareHouseListCreateAPIView.search_fields


class WareHouseDetailAPIView(BaseDetailAPIView):
    serializer_class = WareHouseSerializer
    queryset = WareHouse.objects.all()


class WareHouseUpdateAPIView(BaseUpdateAPIView):
    serializer_class = WareHouseSerializer
    queryset = WareHouse.objects.all()


@method_decorator(never_cache, name='dispatch')
class StockCreateAPIView(BaseAPIMixin, CreateAPIView):
    serializer_class = StockSerializer
    queryset = Stock.objects.all()
    required_permissions = ('warehouse.add_stock',)
    http_method_names = ['post', 'options']

    def perform_create(self, serializer):
        actor = self.request.user.get_username()
        self.save_record(serializer, create_by=actor, modified_by=actor)

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        response.data = {'message': 'Stock created successfully.', 'stock': response.data}
        return response


@method_decorator(never_cache, name='dispatch')
class StockOptionsAPIView(BaseAPIMixin, APIView):
    required_permissions = ('warehouse.add_stock',)
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        return Response({
            'warehouse': [
                {'value': row['pk'], 'label': f"{row['name']} ({row['code']})" if row['code'] else row['name']}
                for row in WareHouse.objects.order_by('name', 'pk').values('pk', 'name', 'code')
            ],
            'product': [
                {'value': row['pk'], 'label': f"{row['sku']} — {row['name']} ({row['unit__code']})"}
                for row in Product.objects.order_by('name', 'pk').values('pk', 'name', 'sku', 'unit__code')
            ],
        })
