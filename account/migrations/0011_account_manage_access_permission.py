from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('account', '0010_remove_account_otp_attempts')]

    operations = [
        migrations.AlterModelOptions(
            name='account',
            options={'permissions': [('manage_access', 'Can manage role assignments and role permissions')]},
        ),
    ]
