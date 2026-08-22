#!/bin/sh
set -eu
cat > /workspace/invoice_math/policy.py <<'PY'
ROUNDING_POLICY = 'half_up'
SUPPORTED_CURRENCIES = {'USD', 'EUR'}
PY
cat > /workspace/invoice_math/rounding.py <<'PY'
from decimal import Decimal, ROUND_HALF_UP
from .policy import ROUNDING_POLICY
def round_money(value: Decimal) -> Decimal:
    if ROUNDING_POLICY != 'half_up':
        raise RuntimeError('rounding policy mismatch')
    return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
PY
