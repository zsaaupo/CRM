import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


def seed_raw_material_type(apps, schema_editor):
    apps.get_model('inventory', 'ProductType').objects.using(schema_editor.connection.alias).get_or_create(
        code='raw-material', defaults={'name': 'Raw Material'},
    )


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('inventory', '0008_product_is_featured'),
        ('warehouse', '0010_stock_counts'),
    ]

    operations = [
        migrations.CreateModel(
            name='RawMaterial',
            fields=[
                ('product', models.OneToOneField(
                    limit_choices_to={'product_type_id': 'raw-material'}, on_delete=django.db.models.deletion.CASCADE,
                    primary_key=True, related_name='raw_material', serialize=False, to='inventory.product',
                )),
                ('warehouse', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                                related_name='raw_materials', to='warehouse.warehouse')),
                ('reorder_level', models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True,
                                                      validators=[django.core.validators.MinValueValidator(0)])),
            ],
            options={
                'constraints': [models.CheckConstraint(
                    condition=models.Q(reorder_level__isnull=True) | models.Q(reorder_level__gte=0),
                    name='raw_material_reorder_level_nonnegative',
                )],
            },
        ),
        migrations.RunPython(seed_raw_material_type, migrations.RunPython.noop),
    ]
