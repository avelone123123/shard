import unittest
from shard_engine import validators
class TestValidators(unittest.TestCase):
    def test_model_id_validation(self):
        self.assertTrue(validators.validate_model_id("Qwen/Qwen2.5-Coder-1.5B"))
        self.assertFalse(validators.validate_model_id("invalid spaces in model"))
    def test_quant_name_validation(self):
        self.assertTrue(validators.validate_quant_name("Q4_K_M"))
        self.assertFalse(validators.validate_quant_name("invalid-quant!"))
