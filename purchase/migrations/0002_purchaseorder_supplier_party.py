import django.db.models.deletion
from django.db import migrations, models


def link_suppliers(apps, schema_editor):
    Order = apps.get_model('purchase', 'PurchaseOrder')
    Party = apps.get_model('people', 'Party')
    database = schema_editor.connection.alias
    parties = Party.all_objects.using(database)
    matches_by_name = {}
    orders = Order.objects.using(database)
    for order in orders.exclude(supplier_legacy__isnull=True).exclude(supplier_legacy='').order_by('pk').iterator():
        name = order.supplier_legacy.strip()
        if not name:
            continue
        if name not in matches_by_name:
            matches = list(parties.filter(name=name, is_supplier=True, is_deleted=False).order_by('pk')[:2])
            if len(matches) == 1:
                supplier = matches[0]
            else:
                # Do not guess between namesakes or change a customer's role.
                code = f'LEGACY-SUP-{order.pk}'
                suffix = 1
                while parties.filter(code__iexact=code).exists():
                    suffix += 1
                    code = f'LEGACY-SUP-{order.pk}-{suffix}'
                supplier = parties.create(code=code, name=name, is_supplier=True,
                                          create_by='migration', modified_by='migration')
            matches_by_name[name] = supplier.pk
        orders.filter(pk=order.pk).update(supplier_id=matches_by_name[name])


def restore_supplier_text(apps, schema_editor):
    Order = apps.get_model('purchase', 'PurchaseOrder')
    orders = Order.objects.using(schema_editor.connection.alias)
    for order in orders.exclude(supplier_id__isnull=True).select_related('supplier').iterator():
        orders.filter(pk=order.pk).update(supplier_legacy=order.supplier.name)


class Migration(migrations.Migration):
    dependencies = [
        ('purchase', '0001_initial'),
        ('people', '0003_party_image'),
    ]

    operations = [
        migrations.RenameField(model_name='purchaseorder', old_name='supplier', new_name='supplier_legacy'),
        migrations.AddField(
            model_name='purchaseorder', name='supplier',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='purchase_orders', to='people.party'),
        ),
        migrations.RunPython(link_suppliers, restore_supplier_text),
        migrations.RemoveField(model_name='purchaseorder', name='supplier_legacy'),
    ]
