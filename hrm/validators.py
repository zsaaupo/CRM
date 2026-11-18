from django.core.exceptions import ValidationError


def validate_working_days(value):
    if (not isinstance(value, list) or not value
            or any(type(day) is not int or not 0 <= day <= 6 for day in value)
            or len(set(value)) != len(value)):
        raise ValidationError('Use a non-empty list of unique weekdays: Monday=0 through Sunday=6.')
