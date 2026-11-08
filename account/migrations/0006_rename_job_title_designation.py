from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('account', '0005_alter_account_address_alter_account_city_and_more'),
    ]

    operations = [
        migrations.RenameField(
            model_name='account',
            old_name='job_title',
            new_name='designation',
        ),
    ]
