import json
from pathlib import Path
import unittest
class VisibleTests(unittest.TestCase):
    def test_index_shape_when_present(self) -> None:
        path = Path('generated/index.json')
        if path.exists():
            self.assertIn('entries', json.loads(path.read_text()))

