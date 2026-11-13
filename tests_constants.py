import unittest
from shard_engine import constants
class TestConstants(unittest.TestCase):
    def test_default_chunk_sizes(self):
        self.assertGreater(constants.DEFAULT_CHUNK_SIZE, 0)
        self.assertGreater(constants.STREAM_CHUNK_SIZE, 0)
    def test_model_states(self):
        self.assertEqual(constants.MODEL_STATE_UNLOADED, "unloaded")
        self.assertEqual(constants.MODEL_STATE_READY, "ready")
    def test_http_status_codes(self):
        self.assertEqual(constants.HTTP_OK, 200)
        self.assertEqual(constants.HTTP_NOT_FOUND, 404)
    def test_supported_quants(self):
        self.assertIn("Q4_K_M", constants.SUPPORTED_QUANTS)
        self.assertIn("Q8_0", constants.SUPPORTED_QUANTS)
if __name__ == "__main__":
    unittest.main()
