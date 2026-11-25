from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.crypto import get_random_string
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from .models import Party


User = get_user_model()


def create_customer_user(actor, party_id: int, *, username: str | None = None, password: str | None = None) -> tuple[Party, bool]:
    if not (actor.is_superuser or has_role_permissions(actor, ('people.create_customer_login',))):
        raise PermissionDenied('Permission people.create_customer_login is required.')
    with transaction.atomic():
        party = Party.objects.select_for_update().filter(pk=party_id, is_customer=True).first()
        if party is None:
            raise NotFound('Customer not found.')
        if party.user_id is not None:
            return party, False
        uname = (username or party.email or f'cust_{party.code.lower()}').strip()
        if User.objects.filter(username__iexact=uname).exists():
            raise ValidationError({'username': f'Username "{uname}" is already in use.'})
        user = User.objects.create_user(
            username=uname,
            email=party.email or '',
            password=password or get_random_string(16),
            is_active=True,
        )
        party.user = user
        party.modified_by = actor.get_username()
        party.save(update_fields=['user', 'modified_by', 'updated_at'])
        return party, True
