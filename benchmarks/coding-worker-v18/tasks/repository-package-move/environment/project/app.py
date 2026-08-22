from acme.billing import invoice_id
from acme.reporting import format_total

def render(number: int, total: int) -> str:
    return f'{invoice_id(number)} {format_total(total)}'
