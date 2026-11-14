from common.view import BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView, BaseUpdateAPIView

from .models import Party
from .serializers import PartyFilterSerializer, PartySerializer


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
