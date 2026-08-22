import unittest
from config_tx import BatchConfigError, ConfigStore

class VisibleTests(unittest.TestCase):
    def test_successful_batch_preserves_order(self) -> None:
        store = ConfigStore({'service': {'port': 80}})
        store.apply_batch([
            {'action': 'set', 'path': 'service.port', 'value': 8080},
            {'action': 'set', 'path': 'service.host', 'value': 'localhost'},
        ])
        self.assertEqual(store.snapshot(), {'service': {'port': 8080, 'host': 'localhost'}})

    def test_invalid_later_operation_rolls_back(self) -> None:
        store = ConfigStore({'service': {'port': 80}})
        before = store.snapshot()
        with self.assertRaises(BatchConfigError):
            store.apply_batch([
                {'action': 'set', 'path': 'service.port', 'value': 8080},
                {'action': 'set', 'path': 'service.port.value', 'value': 1},
            ])
        self.assertEqual(store.snapshot(), before)

