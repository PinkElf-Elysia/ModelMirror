import unittest
from record_pipeline import ProcessingError, process_records

class VisibleTests(unittest.TestCase):
    def test_valid_records_are_normalized(self) -> None:
        result = process_records('upload-7', ['{"id":" 42 ","kind":"USER"}'])
        self.assertEqual(result.values[0]['id'], '42')
        self.assertEqual(result.values[0]['kind'], 'user')

    def test_fail_fast_uses_processing_error(self) -> None:
        with self.assertRaises(ProcessingError):
            process_records('upload-7', ['not-json'])

