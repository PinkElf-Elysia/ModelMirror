from .models import Note
from .ordering import order_notes
def format_notes(notes: list[Note]) -> str:
    return '\n'.join(f'[{item.component}] {item.severity}: {item.title}' for item in order_notes(notes)) + '\n'
