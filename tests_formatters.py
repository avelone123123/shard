import unittest
from shard_engine import formatters
class TestFormatters(unittest.TestCase):
    def test_format_bytes(self):
        self.assertEqual(formatters.format_bytes(512), "512 B")
        self.assertIn("MB", formatters.format_bytes(10485760))
