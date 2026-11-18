from importlib import import_module

import django.db.models.deletion
from django.db import migrations, models


seed_attendance_statuses = import_module(
    'hrm.migrations.0004_employeeattendancestatus_alter_attendance_status',
).seed_attendance_statuses


class Migration(migrations.Migration):
    dependencies = [('hrm', '0004_employeeattendancestatus_alter_attendance_status')]

    operations = [
        # Install the statuses on databases that applied 0004 before it included the seed.
        migrations.RunPython(seed_attendance_statuses, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='attendance',
            name='status',
            field=models.ForeignKey(default='unrecorded', on_delete=django.db.models.deletion.PROTECT,
                                    related_name='employees', to='hrm.employeeattendancestatus', to_field='code'),
        ),
    ]
