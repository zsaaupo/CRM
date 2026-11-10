from django.contrib.auth.models import Permission


def grant_permissions(user, *names):
    for name in names:
        app_label, codename = name.split('.', 1)
        user.user_permissions.add(Permission.objects.get(
            content_type__app_label=app_label, codename=codename,
        ))
