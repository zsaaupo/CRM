from rest_framework import serializers

from inventory.order_csv import order_csv_items, order_csv_template

from .serializers import SalesOrderCreateSerializer, SalesOrderLineSerializer, SalesOrderSerializer


def sale_csv_template():
    return order_csv_template('sale-import-template.csv')


class SalesOrderCSVImportSerializer(SalesOrderCreateSerializer):
    items = None
    file = serializers.FileField(write_only=True)

    class Meta(SalesOrderCreateSerializer.Meta):
        fields = [*SalesOrderSerializer.Meta.fields, 'file']

    def validate(self, attrs):
        upload = attrs.pop('file', None)
        attrs = super().validate(attrs)
        if upload is not None:
            attrs['items'] = order_csv_items(upload, attrs['currency'].code, document='sale',
                                             line_serializer=SalesOrderLineSerializer(),
                                             price_field='sale_price', tax_field='product_tax')
        return attrs
