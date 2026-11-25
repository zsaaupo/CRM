from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

from django.core.exceptions import ValidationError


ZERO = Decimal('0')
QTY_QUANT = Decimal('0.000001')
COST_QUANT = Decimal('0.000001')
TOTAL_COST_QUANT = Decimal('0.0001')


def _to_decimal(value):
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationError('Invalid numeric value.') from exc


def money_quantizer(digits=None):
    if digits is None:
        from .settings_store import get_general
        digits = get_general().decimal_digits
    if digits == 0:
        return Decimal('1')
    return Decimal('1').scaleb(-int(digits))


def q_money(value, digits=None):
    return _to_decimal(value).quantize(money_quantizer(digits), rounding=ROUND_HALF_UP)


def q_qty(value):
    return _to_decimal(value).quantize(QTY_QUANT, rounding=ROUND_HALF_UP)


def q_cost(value):
    return _to_decimal(value).quantize(COST_QUANT, rounding=ROUND_HALF_UP)


def q_total_cost(value):
    return _to_decimal(value).quantize(TOTAL_COST_QUANT, rounding=ROUND_HALF_UP)


def convert_to_base(amount, rate, *, digits=4):
    with localcontext() as ctx:
        ctx.prec = 50
        raw = _to_decimal(amount) * _to_decimal(rate)
        return raw.quantize(Decimal('1').scaleb(-int(digits)), rounding=ROUND_HALF_UP)


def allocate_pro_rata(total, weights, *, digits=None):
    """Allocate `total` across `weights` preserving exact sum; residual lands on last positive-weight entry."""
    total_d = q_money(total, digits=digits)
    weight_ds = [_to_decimal(w) for w in weights]
    if not weight_ds:
        return []
    quant = money_quantizer(digits)
    total_weight = sum(weight_ds, ZERO)
    if total_weight <= ZERO:
        shares = [ZERO.quantize(quant) for _ in weight_ds]
        if total_d != ZERO:
            shares[-1] = total_d
        return shares

    shares = []
    with localcontext() as ctx:
        ctx.prec = 50
        for weight in weight_ds:
            shares.append((total_d * weight / total_weight).quantize(quant, rounding=ROUND_HALF_UP))
    residual = total_d - sum(shares, ZERO)
    if residual != ZERO:
        target_idx = len(shares) - 1
        for idx in range(len(weight_ds) - 1, -1, -1):
            if weight_ds[idx] > ZERO:
                target_idx = idx
                break
        shares[target_idx] = (shares[target_idx] + residual).quantize(quant, rounding=ROUND_HALF_UP)
    return shares
