import unittest
from registry.service import default_registry

class VisibleTests(unittest.TestCase):
    def test_direct_entry(self) -> None:
        self.assertEqual(default_registry().resolve('provider-03').module, 'providers.provider_03')

    def test_transitive_alias(self) -> None:
        self.assertEqual(default_registry().resolve('fast').name, 'provider-07')

