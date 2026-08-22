from decimal import Decimal
import unittest
from invoice_math import invoice_total

class VisibleTests(unittest.TestCase):
    def test_regular_total(self) -> None:
        self.assertEqual(invoice_total([Decimal('1.20'), Decimal('2.35')], 'USD'), Decimal('3.55'))

    def test_currency_validation(self) -> None:
        with self.assertRaisesRegex(ValueError, 'unsupported currency'):
            invoice_total([Decimal('1')], 'JPY')

