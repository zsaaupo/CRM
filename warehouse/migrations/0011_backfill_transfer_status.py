from django.db import migrations
from django.db.models import F


def forward(apps, schema_editor):
    WarehouseTransfer = apps.get_model('warehouse', 'WarehouseTransfer')
    WarehouseTransferItem = apps.get_model('warehouse', 'WarehouseTransferItem')
    WarehouseTransfer.objects.filter(is_completed=True).update(status='completed')
    WarehouseTransferItem.objects.filter(transfer__is_completed=True).update(
        sent_qty=F('base_quantity'),
        received_qty=F('base_quantity'),
    )


class Migration(migrations.Migration):
    dependencies = [
        ('warehouse', '0010_alter_warehousetransfer_options_and_more'),
    ]

    operations = [
        migrations.RunPython(forward, migrations.RunPython.noop),
    ]
