from rest_framework.exceptions import NotFound

from warehouse.scope import lock_warehouse_scope, scoped_queryset

from .models import RawMaterial


def lock_raw_material(pk, actor):
    """Call inside atomic before changing or deleting a material."""
    scope = lock_warehouse_scope(actor)
    records = scoped_queryset(RawMaterial.objects.order_by().select_for_update(), scope)
    try:
        return records.get(pk=pk)
    except RawMaterial.DoesNotExist as exc:
        raise NotFound('Raw material not found.') from exc
