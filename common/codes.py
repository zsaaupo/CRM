"""Server-owned formats. Register another purpose here to share the generation API."""
import secrets
import string
from dataclasses import dataclass

from django.apps import apps
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from account.permissions import has_role_permissions


@dataclass(frozen=True)
class CodeFormat:
    model: str
    field: str
    alphabet: str
    length: int


FORMATS = {
    'coupon': CodeFormat('sale.Coupon', 'code', 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789', 10),
    'gift_card': CodeFormat('sale.GiftCard', 'card_no', string.digits, 16),
}


def generate_code(purpose):
    spec = FORMATS[purpose]
    model = apps.get_model(spec.model)
    for _ in range(20):
        code = ''.join(secrets.choice(spec.alphabet) for _ in range(spec.length))
        if not model.objects.filter(**{spec.field: code}).exists():
            return code
    raise ValidationError('Could not generate an available code. Try again.')


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def generate_code_api(request):
    purpose = request.data.get('purpose')
    if not isinstance(purpose, str) or purpose not in FORMATS:
        raise ValidationError({'purpose': 'Choose a registered code format.'})
    model = apps.get_model(FORMATS[purpose].model)._meta
    if not has_role_permissions(request.user, (f'{model.app_label}.add_{model.model_name}',)):
        raise PermissionDenied()
    return Response({'code': generate_code(purpose)}, headers={'Cache-Control': 'no-store'})

