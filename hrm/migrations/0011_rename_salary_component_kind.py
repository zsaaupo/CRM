from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('hrm', '0010_salary_configuration'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='salarycomponent',
            name='hrm_salary_component_kind_valid',
        ),
        migrations.RenameField(
            model_name='salarycomponent',
            old_name='kind',
            new_name='type',
        ),
        migrations.AddConstraint(
            model_name='salarycomponent',
            constraint=models.CheckConstraint(
                condition=models.Q(type__in=['earning', 'deduction']),
                name='hrm_salary_component_type_valid',
            ),
        ),
    ]
