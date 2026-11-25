from rest_framework import status
from rest_framework.exceptions import APIException, NotFound, ValidationError


class IdempotencyConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = 'This request key has already been used for a different payload.'
    default_code = 'idempotency_conflict'


class InsufficientStock(ValidationError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = 'Insufficient stock for the requested operation.'
    default_code = 'insufficient_stock'


class ScopeViolation(NotFound):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = 'Record not found.'
    default_code = 'not_found'


class StateConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = 'The record is in a state that prevents this action.'
    default_code = 'state_conflict'


class ValidationFailure(ValidationError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = 'Validation failed.'
    default_code = 'validation_failure'
