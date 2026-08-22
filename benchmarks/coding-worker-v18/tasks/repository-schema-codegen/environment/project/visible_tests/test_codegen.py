import unittest
from codegen import generate

class VisibleTests(unittest.TestCase):
    def test_fields_are_sorted(self) -> None:
        result = generate({'title': 'Thing', 'properties': {'z': {'type': 'str'}, 'a': {'type': 'int'}}})
        self.assertLess(result.index('a: int'), result.index('z: str'))
