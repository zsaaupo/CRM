from .models import TradeStatus
from .serializers import TradeStatusSerializer
from .view import BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView, BaseUpdateAPIView


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
