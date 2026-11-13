import os
import tempfile
import unittest
from shard_engine import cache_index
class TestCacheIndex(unittest.TestCase):
    def test_cache_index_scan(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "test_model.gguf")
            with open(fp, "wb") as f:
                f.write(b"GGUF_DATA")
            idx = cache_index.LocalCacheIndex(root_dirs=[td])
            entries = idx.scan()
            self.assertIn("test_model.gguf", entries)
            self.assertTrue(idx.has_weights("test_model.gguf"))
            idx.invalidate()
            self.assertFalse(idx.has_weights("test_model.gguf"))
if __name__ == "__main__":
    unittest.main()
