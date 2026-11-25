from django.db import migrations
from django.db.models import F


def forward(apps, schema_editor):
    ProductType = apps.get_model('inventory', 'ProductType')
    ProductType.objects.get_or_create(
        code='combo',
        defaults={'name': 'Combo', 'description': 'Bundled product built from component items.', 'is_active': True},
    )
    for app_label, model_name in (
        ('sale', 'SalesOrderItem'),
        ('sale', 'SalesQuotationItem'),
        ('purchase', 'PurchaseOrderItem'),
        ('warehouse', 'WarehouseTransferItem'),
    ):
        Model = apps.get_model(app_label, model_name)
        Model.objects.filter(base_quantity__isnull=True).update(base_quantity=F('quantity'))


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0006_productcomboitem_taxrule_warehouseproductprice_and_more'),
        ('warehouse', '0009_warehousetransferitem_base_quantity_and_more'),
        ('purchase', '0010_purchaseorderitem_base_quantity_and_more'),
        ('sale', '0010_salesorderitem_base_quantity_salesorderitem_batch_and_more'),
    ]

    operations = [
        migrations.RunPython(forward, migrations.RunPython.noop),
    ]
