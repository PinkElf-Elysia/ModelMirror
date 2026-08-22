import unittest
from app import render

class VisibleTests(unittest.TestCase):
    def test_render(self) -> None:
        self.assertEqual(render(12, 7), 'INV-000012 Total: 7')
