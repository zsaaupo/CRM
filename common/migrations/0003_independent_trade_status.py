import django.utils.timezone
from django.core.management.color import no_style
from django.db import migrations, models


def copy_statuses(schema_editor, source, target, *, missing_only=False):
    """Copy rows with their audit timestamps, without triggering auto_now."""
    quote = schema_editor.quote_name
    fields = [field.column for field in target._meta.local_fields
              if not missing_only or not field.primary_key]
    columns = ', '.join(quote(field) for field in fields)
    selected = ', '.join(f'source.{quote(field)}' for field in fields)
    sql = (f'INSERT INTO {quote(target._meta.db_table)} ({columns}) '
           f'SELECT {selected} FROM {quote(source._meta.db_table)} source')
    if missing_only:
        sql += (f' WHERE NOT EXISTS (SELECT 1 FROM {quote(target._meta.db_table)} target '
                f'WHERE target.{quote("code")} = source.{quote("code")})')
    schema_editor.execute(sql)
    for statement in schema_editor.connection.ops.sequence_reset_sql(no_style(), [target]):
        schema_editor.execute(statement)


def point_orders_to(apps, schema_editor, old_target, new_target):
    """Rebuild the physical foreign keys; their declared model name is unchanged."""
    for app_label, model_name in [('purchase', 'PurchaseOrder'), ('sale', 'SalesOrder')]:
        model = apps.get_model(app_label, model_name)
        field = model._meta.get_field('status')
        old_field = field.clone()
        old_field.remote_field.model = old_target
        old_field.set_attributes_from_name(field.name)
        old_field.model = model
        new_field = field.clone()
        new_field.remote_field.model = new_target
        new_field.set_attributes_from_name(field.name)
        new_field.model = model
        schema_editor.alter_field(model, old_field, new_field)


def separate_trade_status(apps, schema_editor):
    product_status = apps.get_model('common', 'ProductStatus')
    trade_status = apps.get_model('common', 'TradeStatus')
    schema_editor.create_model(trade_status)
    copy_statuses(schema_editor, product_status, trade_status)
    point_orders_to(apps, schema_editor, product_status, trade_status)


def restore_proxy(apps, schema_editor):
    product_status = apps.get_model('common', 'ProductStatus')
    trade_status = apps.get_model('common', 'TradeStatus')
    # Preserve new trade codes so orders remain valid when returning to shared rows.
    # Existing product labels win where the two independent datasets have diverged.
    copy_statuses(schema_editor, trade_status, product_status, missing_only=True)
    point_orders_to(apps, schema_editor, trade_status, product_status)
    schema_editor.delete_model(trade_status)


class Migration(migrations.Migration):
    dependencies = [
        ('common', '0002_productstatus_tradestatus'),
        ('purchase', '0003_alter_purchaseorder_status'),
        ('sale', '0002_alter_salesorder_status'),
    ]

    operations = [
        # Deleting proxy metadata must not delete/recreate its content type or
        # permissions. The existing trade grants and admin log IDs remain valid.
        migrations.SeparateDatabaseAndState(state_operations=[
            migrations.DeleteModel(name='TradeStatus'),
            migrations.CreateModel(
                name='TradeStatus',
                fields=[
                    ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                    ('created_at', models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                    ('updated_at', models.DateTimeField(auto_now=True)),
                    ('create_by', models.CharField(max_length=255, null=True)),
                    ('modified_by', models.CharField(max_length=255, null=True)),
                    ('code', models.SlugField(max_length=32, unique=True)),
                    ('name', models.CharField(max_length=100)),
                    ('description', models.TextField(blank=True)),
                    ('is_active', models.BooleanField(default=True)),
                ],
                options={'ordering': ['name', 'pk'], 'verbose_name': 'trade status',
                         'verbose_name_plural': 'trade statuses'},
            ),
        ]),
        migrations.RunPython(separate_trade_status, restore_proxy),
    ]
