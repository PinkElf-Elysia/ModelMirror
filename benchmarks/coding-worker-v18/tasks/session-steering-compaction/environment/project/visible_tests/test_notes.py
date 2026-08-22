import unittest
from release_notes.formatter import format_notes
from release_notes.models import Note
class VisibleTests(unittest.TestCase):
    def test_formats_every_note(self) -> None:
        result = format_notes([Note('api', 'normal', 'B'), Note('web', 'high', 'A')])
        self.assertIn('[api] normal: B', result); self.assertIn('[web] high: A', result)

