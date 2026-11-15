import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('common', '0003_independent_trade_status'),
        ('sale', '0002_alter_salesorder_status'),
    ]

    operations = [
        # Refresh the cached former proxy relation before SQLite rebuilds the table.
        migrations.SeparateDatabaseAndState(state_operations=[
            migrations.AlterField(
                model_name='salesorder', name='status',
                field=models.ForeignKey(default='draft', on_delete=django.db.models.deletion.PROTECT,
                    related_name='sales_orders', to='common.tradestatus', to_field='code'),
            ),
        ]),
        migrations.AddField(
            model_name='salesorder', name='is_completed',
            field=models.BooleanField(default=False, editable=False),
        ),
    ]
