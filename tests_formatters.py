import unittest
from shard_engine import formatters
class TestFormatters(unittest.TestCase):
    def test_format_bytes(self):
        self.assertEqual(formatters.format_bytes(512), "512 B")
        self.assertIn("MB", formatters.format_bytes(10485760))
    def test_format_speed(self):
        self.assertTrue(formatters.format_speed(1048576).endswith("/s"))
    def test_format_eta(self):
        self.assertEqual(formatters.format_eta(45), "45s")
        self.assertEqual(formatters.format_eta(125), "2m 5s")
