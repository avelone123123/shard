import unittest
from shard_engine import constants
class TestConstants(unittest.TestCase):
    def test_default_chunk_sizes(self):
        self.assertGreater(constants.DEFAULT_CHUNK_SIZE, 0)
        self.assertGreater(constants.STREAM_CHUNK_SIZE, 0)
    def test_model_states(self):
        self.assertEqual(constants.MODEL_STATE_UNLOADED, "unloaded")
        self.assertEqual(constants.MODEL_STATE_READY, "ready")
