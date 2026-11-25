from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from uuid import UUID

from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F

from .exceptions import IdempotencyConflict
from .models import IdempotencyRecord


@dataclass(frozen=True)
class IdempotentResult:
    result_type: str
    result_id: int
    result_data: dict
    value: object = None


def _normalize(obj):
    if isinstance(obj, Decimal):
        return format(obj.normalize(), 'f') if obj == obj.to_integral() else str(obj.normalize())
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _normalize(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, (list, tuple)):
        return [_normalize(v) for v in obj]
    if isinstance(obj, set):
        return sorted(_normalize(v) for v in obj)
    return obj


def canonical_payload_hash(payload) -> str:
    serialized = json.dumps(_normalize(payload), sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def run_idempotent(key, scope: str, payload, fn, *, loader=None):
    """
    Execute `fn` atomically once per UUID `key` + `scope`.
    - Same key + same scope + same canonical payload hash -> returns replay (`loader(record)` or `record.result_data`), created=False.
    - Same key + different scope or payload hash -> raises IdempotencyConflict (HTTP 409).
    - `fn()` may return an `IdempotentResult` or a tuple `(result_type, result_id, result_data[, value])`.
    """
    if not isinstance(key, UUID):
        key = UUID(str(key))
    payload_hash = canonical_payload_hash(payload)

    with transaction.atomic():
        # Acquire write lock on existing record if present (handles SQLite + PostgreSQL).
        IdempotencyRecord.objects.filter(key=key).update(scope=F('scope'))
        existing = IdempotencyRecord.objects.select_for_update().filter(key=key).first()
        if existing is not None:
            if existing.scope != scope or existing.payload_hash != payload_hash:
                raise IdempotencyConflict()
            if loader is not None:
                return loader(existing), False
            return existing.result_data, False

        outcome = fn()
        if isinstance(outcome, IdempotentResult):
            result_type = outcome.result_type
            result_id = outcome.result_id
            result_data = outcome.result_data
            value = outcome.value if outcome.value is not None else outcome.result_data
        elif isinstance(outcome, tuple) and len(outcome) in (3, 4):
            result_type, result_id, result_data = outcome[:3]
            value = outcome[3] if len(outcome) == 4 else result_data
        else:
            raise TypeError('Idempotent callback must return IdempotentResult or (result_type, result_id, result_data[, value]).')

        try:
            with transaction.atomic():
                record = IdempotencyRecord.objects.create(
                    key=key,
                    scope=scope,
                    payload_hash=payload_hash,
                    result_type=result_type,
                    result_id=int(result_id),
                    result_data=result_data or {},
                )
        except IntegrityError as exc:
            existing = IdempotencyRecord.objects.filter(key=key).first()
            if existing is not None and existing.scope == scope and existing.payload_hash == payload_hash:
                return (loader(existing) if loader else existing.result_data), False
            raise IdempotencyConflict() from exc

        if loader is not None and value is None:
            return loader(record), True
        return value, True
