import math
import os
import tempfile
import unittest
from hardware import estimate_model_params, evaluate_compatibility, get_hardware_profile
from hf_hub import CURATED_MODELS, browse_models, fetch_model_metadata
from shard_bridge import ShardNativeBridge
class ShardKernelAndRuntimeSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge = ShardNativeBridge()
    def test_01_native_dll_loaded(self):
        self.assertTrue(self.bridge.is_native_active)
        self.assertIn("Shard Native Core", self.bridge.version)
    def test_02_kernel_registry_populated(self):
        self.assertGreaterEqual(self.bridge.kernel_count(), 16)
        ids = self.bridge.kernel_ids()
        self.assertIn("KRN-001", ids)
        self.assertIn("KRN-016", ids)
    def test_03_dot_product_avx2_standard(self):
        a = [float(i + 1) * 0.25 for i in range(67)]
        b = [float(70 - i) * 0.1 for i in range(67)]
        ref = sum(x * y for x, y in zip(a, b))
        got = self.bridge.dot_product(a, b, fast=False)
        self.assertAlmostEqual(got, ref, places=2)
    def test_04_dot_product_unroll8_dual_stream(self):
        a = [float((i % 19) - 9) * 0.15 for i in range(133)]
        b = [float((i % 13) - 6) * 0.22 for i in range(133)]
        ref = sum(x * y for x, y in zip(a, b))
        got = self.bridge.dot_product(a, b, fast=True)
        self.assertAlmostEqual(got, ref, places=2)
    def test_05_scale_vector_avx2(self):
        v = [float(i) - 10.0 for i in range(37)]
        out = self.bridge.scale_vector(v, 2.5)
        for g, x in zip(out, v):
            self.assertAlmostEqual(g, x * 2.5, places=4)
    def test_06_rmsnorm_f32_affine(self):
        x = [0.2 * (i + 1) for i in range(33)]
        w = [1.5 for _ in range(33)]
        out = self.bridge.rmsnorm(x, w, eps=1e-5)
        rms = 1.0 / math.sqrt(sum(v * v for v in x) / len(x) + 1e-5)
        for g, v in zip(out, x):
            self.assertAlmostEqual(g, v * rms * 1.5, places=3)
    def test_07_layernorm_f32_zero_mean(self):
        x = [float(i * 2 - 15) for i in range(35)]
        g = [1.0 for _ in range(35)]
        b = [0.0 for _ in range(35)]
        out = self.bridge.layernorm(x, g, b, eps=1e-5)
        self.assertAlmostEqual(sum(out) / len(out), 0.0, places=3)
    def test_08_swiglu_fused_gate(self):
        gate = [-1.0, 0.0, 1.0, 2.0, 0.5, -0.5, 1.5, -1.5, 0.25]
        up = [2.0 for _ in gate]
        out = self.bridge.swiglu(gate, up)
        for o, g in zip(out, gate):
            ref = (g / (1.0 + math.exp(-g))) * 2.0
            self.assertAlmostEqual(o, ref, places=4)
    def test_09_vec_add_masm_avx2(self):
        a = [float(i) for i in range(45)]
        b = [float(100 - i) for i in range(45)]
        out = self.bridge.vec_add(a, b)
        self.assertTrue(all(abs(v - 100.0) < 1e-4 for v in out))
    def test_10_hadamard_masm_avx2(self):
        a = [float(i + 1) for i in range(29)]
        b = [0.5 for _ in range(29)]
        out = self.bridge.hadamard(a, b)
        for o, x in zip(out, a):
            self.assertAlmostEqual(o, x * 0.5, places=4)
    def test_11_axpy_masm_avx2(self):
        x = [2.0 for _ in range(41)]
        y = [3.0 for _ in range(41)]
        out = self.bridge.axpy(x, y, 4.0)
        self.assertTrue(all(abs(v - 11.0) < 1e-4 for v in out))
    def test_12_silu_activation(self):
        x = [-2.0, -0.5, 0.0, 0.5, 2.0]
        out = self.bridge.silu(x)
        self.assertAlmostEqual(out[2], 0.0, places=5)
        self.assertGreater(out[4], 1.7)
    def test_13_gelu_activation(self):
        x = [-3.0, 0.0, 1.0, 3.0]
        out = self.bridge.gelu(x)
        self.assertAlmostEqual(out[1], 0.0, places=5)
        self.assertGreater(out[2], 0.8)
    def test_14_softmax_numerical_stability(self):
        logits = [1000.0, 1001.0, 1002.0, 999.0, 998.0, 1000.5, 997.0, 1001.5, 1000.2]
        probs = self.bridge.softmax(logits)
        self.assertAlmostEqual(sum(probs), 1.0, places=4)
        self.assertEqual(probs.index(max(probs)), 2)
    def test_15_rope_norm_preservation(self):
        vec = [0.6, 0.8, -0.5, 1.2, 0.3, -0.4, 1.0, 0.0]
        rot = self.bridge.rope(vec, pos=11, theta=10000.0)
        orig_norm = sum(v * v for v in vec)
        rot_norm = sum(v * v for v in rot)
        self.assertAlmostEqual(orig_norm, rot_norm, places=3)
    def test_16_cosine_similarity_identical_and_orthogonal(self):
        v1 = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
        self.assertAlmostEqual(self.bridge.cosine_sim(v1, v1), 1.0, places=4)
        o1 = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0]
        o2 = [0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0]
        self.assertAlmostEqual(self.bridge.cosine_sim(o1, o2), 0.0, places=4)
    def test_17_dequant_q4_nibble_unpack(self):
        raw = bytes([0x98] * 10)
        out = self.bridge.dequant_q4(raw, scale=0.5)
        self.assertEqual(len(out), 20)
        self.assertAlmostEqual(out[0], 0.0, places=4)
        self.assertAlmostEqual(out[1], 0.5, places=4)
    def test_18_dequant_q8_avx2_unpack(self):
        raw = bytes([10, 20, 30, 40, 50, 60, 70, 80, 90])
        out = self.bridge.dequant_q8(raw, scale=0.1)
        self.assertAlmostEqual(out[0], 1.0, places=4)
        self.assertAlmostEqual(out[8], 9.0, places=4)
    def test_19_win32_mmap_zero_copy_roundtrip(self):
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(b"SHARD_MMAP_TEST_PAYLOAD_2026" * 64)
            tpath = tf.name
        try:
            ptr, sz = self.bridge.mmap_open_file(tpath)
            self.assertIsNotNone(ptr)
            self.assertEqual(sz, len(b"SHARD_MMAP_TEST_PAYLOAD_2026") * 64)
            closed = self.bridge.mmap_close_view(ptr)
            self.assertTrue(closed)
        finally:
            if os.path.exists(tpath):
                os.remove(tpath)
    def test_20_hardware_tier_matrix(self):
        hw_high = {"vram_total_mb": 24576, "ram_total_mb": 65536}
        c_perf = evaluate_compatibility("Qwen/Qwen2.5-Coder-7B-Instruct", hw_high)
        self.assertEqual(c_perf["tier"], "PERFECT")
        hw_low = {"vram_total_mb": 2048, "ram_total_mb": 8192}
        c_nvme = evaluate_compatibility("meta-llama/Llama-3.3-70B-Instruct", hw_low)
        self.assertEqual(c_nvme["tier"], "NVME_STREAM")
    def test_21_model_param_estimator(self):
        self.assertEqual(estimate_model_params("Qwen/Qwen2.5-Coder-32B-Instruct"), 32.0)
        self.assertEqual(estimate_model_params("HuggingFaceTB/SmolLM2-360M-Instruct"), 0.35)
    def test_22_curated_catalog_and_metadata(self):
        self.assertGreaterEqual(len(CURATED_MODELS), 25)
        hw = get_hardware_profile()
        meta = fetch_model_metadata("Qwen/Qwen2.5-Coder-7B-Instruct", hw)
        self.assertEqual(meta["author"], "Qwen")
    def test_krn_017_hf_metadata_tier_hydration(self):
        self.assertGreaterEqual(self.bridge.kernel_count(), 17)
        self.assertIn("KRN-017", self.bridge.kernel_ids())
        self.assertIn('compatibility', fetch_model_metadata('deepseek-ai/DeepSeek-R1-Distill-Qwen-7B', {'vram_total_mb': 12288, 'ram_total_mb': 32768}))
    def test_krn_018_desktop_studio_binaries_present(self):
        self.assertGreaterEqual(self.bridge.kernel_count(), 18)
        self.assertIn("KRN-018", self.bridge.kernel_ids())
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.assertTrue(os.path.exists(os.path.join(base_dir, "shard-native.exe")))
        self.assertTrue(os.path.exists(os.path.join(base_dir, "shard.exe")))
    def test_23_decimal_param_estimator_precision(self):
        self.assertEqual(estimate_model_params("Qwen/Qwen3-0.6B"), 0.6)
        self.assertEqual(estimate_model_params("Qwen/Qwen2.5-Coder-1.5B-Instruct"), 1.5)
        self.assertEqual(estimate_model_params("openai-community/gpt2"), 0.15)
        comp_4gb = evaluate_compatibility("Qwen/Qwen3-0.6B", {"vram_total_mb": 4096, "ram_total_mb": 8192})
        self.assertEqual(comp_4gb["tier"], "PERFECT")
    def test_24_ideal_hardware_catalog_filter(self):
        hw_4gb = {"vram_total_mb": 4096, "ram_total_mb": 8192}
        ideal = browse_models(category="ideal", hw=hw_4gb)
        self.assertGreater(len(ideal), 0)
        self.assertEqual(ideal[0]["compatibility"]["tier"], "PERFECT")
if __name__ == "__main__":
    unittest.main()
