from decimal import Decimal
from .policy import SUPPORTED_CURRENCIES
from .rounding import round_money

def invoice_total(amounts: list[Decimal], currency: str) -> Decimal:
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError('unsupported currency')
    return round_money(sum(amounts, Decimal('0')))
