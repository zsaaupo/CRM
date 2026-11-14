from django.db import transaction
from rest_framework.exceptions import APIException, NotFound, PermissionDenied

from .models import SalesOrder


class SalesOrderLocked(APIException):
    status_code = 409
    default_detail = 'Only draft sales orders can be changed or deleted.'


def lock_draft_order(pk):
    """All order and line writes share the parent lock; stock is never changed."""
    try:
        order = SalesOrder.objects.select_for_update().get(pk=pk)
    except SalesOrder.DoesNotExist as exc:
        raise NotFound('Sales order not found.') from exc
    if order.status_id != 'draft':
        raise SalesOrderLocked()
    return order


def require_item_permission(request, action):
    if request is not None and not request.user.has_perm(f'sale.{action}_salesorderitem'):
        raise PermissionDenied(f'You do not have permission to {action} sales order items.')


@transaction.atomic
def delete_order(order, request):
    order = lock_draft_order(order.pk)
    if order.items.exists():
        require_item_permission(request, 'delete')
        order.items.all().delete()
    order.delete()
