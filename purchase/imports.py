from rest_framework import serializers

from inventory.order_csv import order_csv_items, order_csv_template

from .serializers import PurchaseOrderCreateSerializer, PurchaseOrderLineSerializer, PurchaseOrderSerializer


def purchase_csv_template():
    return order_csv_template('purchase-import-template.csv')


class PurchaseOrderCSVImportSerializer(PurchaseOrderCreateSerializer):
    items = None
    file = serializers.FileField(write_only=True)

    class Meta(PurchaseOrderCreateSerializer.Meta):
        fields = [*PurchaseOrderSerializer.Meta.fields, 'file']

    def validate(self, attrs):
        upload = attrs.pop('file', None)
        attrs = super().validate(attrs)
        if upload is not None:
            attrs['items'] = order_csv_items(upload, attrs['currency'].code, document='purchase',
                                             line_serializer=PurchaseOrderLineSerializer(), price_field='cost_price')
        return attrs
