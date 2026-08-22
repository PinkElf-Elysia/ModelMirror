from .models import Note
def order_notes(notes: list[Note]) -> list[Note]:
    return sorted(notes, key=lambda item: (item.component, item.title))
