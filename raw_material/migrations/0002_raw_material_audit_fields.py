from decimal import Decimal

import django.core.validators
import django.utils.timezone
from django.db import migrations, models


def copy_audit_fields(apps, schema_editor):
    database = schema_editor.connection.alias
    products = apps.get_model('inventory', 'Product').objects.using(database).filter(pk=models.OuterRef('product_id'))
    materials = apps.get_model('raw_material', 'RawMaterial').objects.using(database)
    materials.update(**{
        field: models.Subquery(products.values(field)[:1])
        for field in ('created_at', 'updated_at', 'create_by', 'modified_by')
    })


class Migration(migrations.Migration):
    dependencies = [('raw_material', '0001_initial')]

    operations = [
        migrations.AlterModelOptions(name='rawmaterial', options={'ordering': ['product__name', 'pk']}),
        migrations.AddField(
            model_name='rawmaterial', name='created_at',
            field=models.DateTimeField(default=django.utils.timezone.now, editable=False),
        ),
        migrations.AddField(model_name='rawmaterial', name='updated_at', field=models.DateTimeField(auto_now=True, null=True)),
        migrations.AddField(model_name='rawmaterial', name='create_by', field=models.CharField(max_length=255, null=True)),
        migrations.AddField(model_name='rawmaterial', name='modified_by', field=models.CharField(max_length=255, null=True)),
        migrations.RunPython(copy_audit_fields, migrations.RunPython.noop),
        migrations.AlterField(model_name='rawmaterial', name='updated_at', field=models.DateTimeField(auto_now=True)),
        migrations.AlterField(
            model_name='rawmaterial', name='reorder_level',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True,
                                      validators=[django.core.validators.MinValueValidator(Decimal('0'))]),
        ),
    ]
