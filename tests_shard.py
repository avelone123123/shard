"""Tests for shard: fit advisor, GGUF selection, settings, module compile.

All expected values below were verified empirically against the live module
(scratch runs on Python 3.14, Windows). Run with:

    python -m unittest tests_shard.py
"""
import json
import os
import py_compile
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

# settings.py reads SHARD_DATA_DIR at import time -> must be set first
_TMP_DATA = tempfile.mkdtemp(prefix="shard_test_data_")
os.environ["SHARD_DATA_DIR"] = _TMP_DATA

import fit  # noqa: E402
import gguf_backend as g  # noqa: E402
import settings  # noqa: E402


# ---------------------------------------------------------------------------
# quant names
# ---------------------------------------------------------------------------
class TestQuantNames(unittest.TestCase):
    def test_parse_iq_quants(self):
        # IQ files were silently skipped before the _QUANT_RE fix
        self.assertEqual(g.parse_quant_from_name("model-IQ4_XS.gguf"), "IQ4_XS")
        self.assertEqual(g.parse_quant_from_name("model-iq4_xs.gguf"), "IQ4_XS")
        self.assertEqual(g.parse_quant_from_name("model-IQ4_NL.gguf"), "IQ4_NL")
        self.assertEqual(g.parse_quant_from_name("model-IQ2_M.gguf"), "IQ2_M")
        self.assertEqual(g.parse_quant_from_name("model-IQ1_S.gguf"), "IQ1_S")

    def test_parse_rejects_non_quant(self):
        self.assertIsNone(g.parse_quant_from_name("model-q4.gguf"))
        self.assertIsNone(g.parse_quant_from_name("Qwen2.5-7B-Instruct.gguf"))

    def test_parse_standard(self):
        self.assertEqual(g.parse_quant_from_name("model-Q8_0.gguf"), "Q8_0")
        self.assertEqual(g.parse_quant_from_name("model-Q6_K.gguf"), "Q6_K")
        self.assertEqual(g.parse_quant_from_name("model-Q2_K_S.gguf"), "Q2_K_S")
        self.assertEqual(g.parse_quant_from_name("model-Q4_K_M.gguf"), "Q4_K_M")
        self.assertEqual(
            g.parse_quant_from_name("model-Q5_K_M-00001-of-00003.gguf"), "Q5_K_M")

    def test_parse_float16_names(self):
        self.assertEqual(g.parse_quant_from_name("model-F16.gguf"), "F16")
        self.assertEqual(g.parse_quant_from_name("model-bf16.gguf"), "F16")
        self.assertEqual(g.parse_quant_from_name("model.f16.gguf"), "F16")

    def test_normalize(self):
        self.assertEqual(g.normalize_quant("q4-k-m"), "Q4_K_M")
        self.assertEqual(g.normalize_quant("Q4KM"), "Q4_K_M")
        self.assertEqual(g.normalize_quant("Q4_K_M"), "Q4_K_M")
        self.assertEqual(g.normalize_quant(" q5_k_m "), "Q5_K_M")
        self.assertIsNone(g.normalize_quant("garbage"))
        self.assertIsNone(g.normalize_quant(""))
        self.assertIsNone(g.normalize_quant(None))


# ---------------------------------------------------------------------------
# parameter / KV-cache math
# ---------------------------------------------------------------------------
class TestParams(unittest.TestCase):
    QWEN3B = {"hidden_size": 2048, "num_hidden_layers": 36,
              "num_attention_heads": 16, "num_key_value_heads": 2,
              "head_dim": 128, "intermediate_size": 11008,
              "vocab_size": 152064, "tie_word_embeddings": True}

    def test_tied_vs_untied(self):
        self.assertEqual(fit.params_from_config(self.QWEN3B), (3.09, "config"))
        untied = dict(self.QWEN3B, tie_word_embeddings=False)
        self.assertEqual(fit.params_from_config(untied), (3.4, "config"))

    def test_gemma9(self):
        cfg = {"hidden_size": 3584, "num_hidden_layers": 42,
               "num_attention_heads": 16, "num_key_value_heads": 8,
               "head_dim": 256, "intermediate_size": 14336,
               "vocab_size": 256000, "tie_word_embeddings": True}
        self.assertEqual(fit.params_from_config(cfg), (9.24, "config"))

    def test_moe_counts_experts(self):
        cfg = {"hidden_size": 2048, "num_hidden_layers": 1,
               "num_attention_heads": 16, "head_dim": 128,
               "intermediate_size": 1408, "num_local_experts": 8,
               "shared_expert_intermediate_size": 1408,
               "vocab_size": 1000, "tie_word_embeddings": True}
        self.assertEqual(fit.params_from_config(cfg), (0.1, "config"))

    def test_defaults_fill_missing(self):
        cfg = {"hidden_size": 1024, "num_hidden_layers": 8,
               "num_attention_heads": 8, "intermediate_size": 2752,
               "vocab_size": 4096}
        self.assertEqual(fit.params_from_config(cfg), (0.11, "config"))

    def test_raises_without_essentials(self):
        for bad in ({}, {"hidden_size": 2048, "num_attention_heads": 16}):
            with self.assertRaises(ValueError):
                fit.params_from_config(bad)


class TestKVCache(unittest.TestCase):
    CFG = {"hidden_size": 1024, "num_hidden_layers": 28,
           "num_attention_heads": 16, "num_key_value_heads": 8, "head_dim": 128}

    def test_qwen06_ctx4096(self):
        self.assertEqual(fit.kv_cache_bytes_from_config(self.CFG, 4096),
                         469762048)

    def test_tiny_ctx(self):
        self.assertEqual(fit.kv_cache_bytes_from_config(self.CFG, 10), 29360128)

    def test_empty_config(self):
        self.assertEqual(fit.kv_cache_bytes_from_config({}, 4096), 0)


# ---------------------------------------------------------------------------
# bandwidth model
# ---------------------------------------------------------------------------
class TestEst(unittest.TestCase):
    def test_pure_gpu(self):
        self.assertEqual(fit.est_tok_s(1e9, 0), 90.0)

    def test_hybrid(self):
        self.assertEqual(fit.est_tok_s(0.5e9, 0.5e9), 55.4)

    def test_disk_stream(self):
        self.assertEqual(fit.est_tok_s(0, 0, 2.5e9), 1.0)
        self.assertEqual(fit.est_tok_s(0, 0, 0), 0.0)

    def test_quant_size_bytes(self):
        self.assertEqual(fit.quant_size_bytes(1.0, 8.0), 1e9)


class TestPlanQuant(unittest.TestCase):
    CFG10 = {"num_hidden_layers": 10}

    def test_gpu_full_fit(self):
        p = fit._plan_quant(1.0, self.CFG10, 0, 8.0, 2e9, 1e9)
        self.assertEqual(p["verdict"], "GPU")
        self.assertEqual(p["ngl"], 999)
        self.assertEqual(p["tok_s"], 90.0)

    def test_hybrid_split(self):
        p = fit._plan_quant(1.0, self.CFG10, 0, 8.0, 0.5e9, 1e9)
        self.assertEqual(p["verdict"], "HYBRID")
        self.assertEqual(p["ngl"], 5)
        self.assertAlmostEqual(p["vram_gb"], 0.5, places=2)
        self.assertAlmostEqual(p["ram_gb"], 0.5, places=2)
        self.assertEqual(p["tok_s"], 55.4)

    def test_impossible_by_ram(self):
        p = fit._plan_quant(1.0, self.CFG10, 0, 8.0, 0.5e9, 0.1e9)
        self.assertEqual(p["verdict"], "IMPOSSIBLE")
        self.assertAlmostEqual(p["ram_gb"], 0.5, places=2)

    def test_impossible_tiny_vram(self):
        p = fit._plan_quant(1.0, self.CFG10, 0, 8.0, 5e7, 1e9)
        self.assertEqual(p["verdict"], "IMPOSSIBLE")
        self.assertAlmostEqual(p["ram_gb"], 1.0, places=2)

    def test_hybrid_with_kv(self):
        p = fit._plan_quant(1.0, self.CFG10, 100e6, 8.0, 5e8, 1e9)
        self.assertEqual(p["verdict"], "HYBRID")
        self.assertEqual(p["ngl"], 4)
        self.assertAlmostEqual(p["vram_gb"], 0.44, places=2)
        self.assertAlmostEqual(p["ram_gb"], 0.66, places=2)
        self.assertEqual(p["tok_s"], 46.8)
        self.assertAlmostEqual(p["total_gb"], 1.1, places=2)


class TestBudgets(unittest.TestCase):
    def test_mb_to_bytes(self):
        self.assertEqual(
            fit._budgets({"vram_free_mb": 3500, "ram_avail_mb": 12000}),
            (3250585600, 10435428352))

    def test_negative_clamped(self):
        self.assertEqual(
            fit._budgets({"vram_free_mb": -5, "ram_avail_mb": -5}), (0, 0))


class TestFallbackCfg(unittest.TestCase):
    def test_27b_fields(self):
        cfg, src = fit._fallback_cfg("x/27b", 27.0, 4096)
        self.assertEqual(src, "name-regex")
        self.assertEqual(cfg["hidden_size"], 4096)
        self.assertEqual(cfg["num_hidden_layers"], 64)
        self.assertEqual(cfg["num_attention_heads"], 32)
        self.assertEqual(cfg["num_key_value_heads"], 32)
        self.assertEqual(cfg["intermediate_size"], 21708)
        self.assertEqual(cfg["vocab_size"], 32000)
        self.assertTrue(cfg["tie_word_embeddings"])

    def test_small_models(self):
        c03, _ = fit._fallback_cfg("x/0.3b", 0.3, 4096)
        self.assertEqual((c03["hidden_size"], c03["num_hidden_layers"],
                          c03["num_attention_heads"]), (768, 24, 6))
        c2, _ = fit._fallback_cfg("x/2b", 2.0, 4096)
        self.assertEqual((c2["hidden_size"], c2["num_hidden_layers"]),
                         (2048, 36))


# ---------------------------------------------------------------------------
# fit_report integration (fetch=False, no network)
# ---------------------------------------------------------------------------
HW_OK = {"vram_free_mb": 8000, "ram_avail_mb": 16000, "vram_total_mb": 8192,
         "ram_total_mb": 16384, "gpu_name": "RTX 3060", "disk_free_gb": 200.0}


class TestFitReport(unittest.TestCase):
    def test_gpu_full_fit(self):
        r = fit.fit_report("tiny-360m-gpu-fit", hw=HW_OK, ctx_tokens=4096,
                           fetch=False)
        self.assertEqual(r["params_b"], 0.35)
        self.assertEqual(r["layers"], 24)
        self.assertAlmostEqual(r["fp16_gb"], 1.0, places=2)
        self.assertAlmostEqual(r["kv_gb"], 0.3, places=2)
        self.assertEqual(r["verdict"], "GPU")
        self.assertEqual(r["recommended"], "Q8_0")
        self.assertEqual(r["ngl"], 999)
        self.assertEqual(r["tok_s_est"], 133.6)
        self.assertAlmostEqual(r["gguf_size_gb"], 0.37, places=2)
        self.assertTrue(r["disk_ok"])
        self.assertIn("полностью помещается", r["message_ru"])
        self.assertIn("KV-кэш", r["message_ru"])

    def test_hybrid(self):
        r = fit.fit_report("test/phi-4", hw=HW_OK, ctx_tokens=4096, fetch=False)
        self.assertEqual(r["params_b"], 14.0)
        self.assertEqual(r["verdict"], "HYBRID")
        self.assertEqual(r["recommended"], "Q2_K")
        self.assertEqual(r["ngl"], 44)
        self.assertEqual(r["tok_s_est"], 9.5)
        self.assertAlmostEqual(r["gguf_size_gb"], 5.34, places=2)
        self.assertAlmostEqual(r["kv_gb"], 3.22, places=2)
        self.assertIn("гибрид", r["message_ru"])
        rows = {q["quant"]: q for q in r["quants"]}
        self.assertEqual((rows["Q8_0"]["ngl"], rows["Q8_0"]["tok_s"],
                          rows["Q8_0"]["ram_gb"]), (21, 2.9, 10.18))
        self.assertEqual((rows["Q2_K"]["ngl"], rows["Q2_K"]["tok_s"],
                          rows["Q2_K"]["ram_gb"]), (44, 9.5, 0.71))

    def test_disk_veto(self):
        hw = dict(HW_OK, disk_free_gb=1.0)
        r = fit.fit_report("test/phi-4-disk-veto", hw=hw, ctx_tokens=4096,
                           fetch=False)
        self.assertEqual(r["verdict"], "IMPOSSIBLE")
        self.assertEqual(r["recommended"], "Q2_K")
        self.assertEqual(r["ngl"], 0)
        self.assertEqual(r["tok_s_est"], 0.0)
        self.assertIsNone(r["gguf_size_gb"])
        self.assertFalse(r["disk_ok"])
        self.assertIn("НЕ ЗАПУСТИТСЯ", r["message_ru"])
        self.assertNotIn("KV-кэш", r["message_ru"])
        self.assertIn("SSD", r["mmap_note"])

    def test_impossible_rtx2050_27b(self):
        hw = {"vram_free_mb": 3500, "ram_avail_mb": 12000,
              "vram_total_mb": 4096, "ram_total_mb": 12288,
              "gpu_name": "RTX 2050", "disk_free_gb": 200.0}
        r = fit.fit_report("google/gemma-2-27b-it", hw=hw, ctx_tokens=4096,
                           fetch=False)
        self.assertEqual(r["params_b"], 27.0)
        self.assertEqual(r["params_source"], "name-regex")
        self.assertEqual(r["layers"], 64)
        self.assertAlmostEqual(r["fp16_gb"], 58.29, places=2)
        self.assertAlmostEqual(r["kv_gb"], 4.29, places=2)
        self.assertEqual(r["verdict"], "IMPOSSIBLE")
        self.assertEqual(r["recommended"], "Q2_K")
        self.assertEqual(r["ngl"], 0)
        self.assertEqual(r["tok_s_est"], 0.0)
        self.assertIsNone(r["gguf_size_gb"])
        self.assertTrue(r["disk_ok"])
        self.assertIn("SSD", r["mmap_note"])
        self.assertIn("~0.0 ток/с", r["mmap_note"])
        self.assertIn("НЕ ЗАПУСТИТСЯ", r["message_ru"])


# ---------------------------------------------------------------------------
# ngl mapping
# ---------------------------------------------------------------------------
class TestComputeNgl(unittest.TestCase):
    def test_gpu_full(self):
        self.assertEqual(g.compute_ngl("GPU", 0), 999)

    def test_hybrid(self):
        self.assertEqual(g.compute_ngl("HYBRID", 44), 44)
        self.assertEqual(g.compute_ngl("HYBRID", -3), 0)

    def test_other_zero(self):
        self.assertEqual(g.compute_ngl("IMPOSSIBLE", 5), 0)


# ---------------------------------------------------------------------------
# GGUF file selection
# ---------------------------------------------------------------------------
class TestSelectGguf(unittest.TestCase):
    MULTI = [("dir/model-Q4_K_M-00001-of-00003.gguf", 300),
             ("dir/model-Q4_K_M-00002-of-00003.gguf", 300),
             ("dir/model-Q4_K_M-00003-of-00003.gguf", 300)]

    def test_multipart_grouping(self):
        r = g.select_gguf_file(self.MULTI)
        self.assertEqual(r["quant"], "Q4_K_M")
        self.assertEqual(r["size_bytes"], 900)
        self.assertTrue(r["multipart"])
        self.assertEqual(len(r["parts"]), 3)
        self.assertEqual(r["path"], "dir/model-Q4_K_M-00001-of-00003.gguf")

    def test_wanted_exact(self):
        r = g.select_gguf_file(self.MULTI, wanted_quant="Q4_K_M")
        self.assertEqual(r["quant"], "Q4_K_M")
        self.assertEqual(r["size_bytes"], 900)

    def test_wanted_falls_to_smaller(self):
        two = [("m-Q4_K_M.gguf", 485), ("m-Q8_0.gguf", 850)]
        r = g.select_gguf_file(two, wanted_quant="Q5_K_M")
        self.assertEqual(r["quant"], "Q4_K_M")

    def test_wanted_smaller_not_available(self):
        r = g.select_gguf_file([("m-Q8_0.gguf", 850)], wanted_quant="Q2_K")
        self.assertEqual(r["quant"], "Q8_0")

    def test_prefer_max_bpw(self):
        two = [("m-Q4_K_M.gguf", 485), ("m-Q8_0.gguf", 850)]
        self.assertEqual(
            g.select_gguf_file(two, prefer_max_bpw=5.0)["quant"], "Q4_K_M")
        # 1.0 fits nothing -> plain best-quant fall-through
        self.assertEqual(
            g.select_gguf_file(two, prefer_max_bpw=1.0)["quant"], "Q8_0")

    def test_prefer_single_over_multipart(self):
        mix = [("big-Q8_0-00001-of-00002.gguf", 400),
               ("big-Q8_0-00002-of-00002.gguf", 400), ("s-Q8_0.gguf", 800)]
        r = g.select_gguf_file(mix)
        self.assertEqual(r["path"], "s-Q8_0.gguf")
        self.assertFalse(r["multipart"])

    def test_multipart_best_wins_without_single_same_quant(self):
        mix = [("big-Q8_0-00001-of-00002.gguf", 400),
               ("big-Q8_0-00002-of-00002.gguf", 400), ("s-Q4_K_M.gguf", 485)]
        r = g.select_gguf_file(mix)
        self.assertTrue(r["multipart"])
        self.assertEqual(r["size_bytes"], 800)
        self.assertEqual(len(r["parts"]), 2)

    def test_empty_and_unparseable(self):
        self.assertIsNone(g.select_gguf_file([]))
        self.assertIsNone(g.select_gguf_file(
            [("Qwen2.5-7B-Instruct.gguf", 100), ("model-q4.gguf", 100)]))

    def test_nonfirst_parts_only(self):
        self.assertIsNone(g.select_gguf_file(
            [("dir/m-00002-of-00003.gguf", 300), ("dir/m-00003-of-00003.gguf", 300)]))


# ---------------------------------------------------------------------------
# settings (custom models dir)
# ---------------------------------------------------------------------------
class TestSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.custom = os.path.join(_TMP_DATA, "models_custom")
        os.makedirs(cls.custom, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        try:
            settings.reset_models_dir()
        except Exception:
            pass

    def setUp(self):
        self.addCleanup(settings.reset_models_dir)

    def test_default(self):
        self.assertEqual(settings.models_dir(), settings.DEFAULT_MODELS_DIR)
        self.assertFalse(settings.is_custom_models_dir())

    def test_set_and_persist(self):
        got = settings.set_models_dir(self.custom)
        self.assertEqual(got, os.path.abspath(self.custom))
        self.assertEqual(settings.models_dir(), got)
        self.assertTrue(settings.is_custom_models_dir())
        with open(settings._SETTINGS_FILE, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f)["models_dir"], got)

    def test_reset(self):
        settings.set_models_dir(self.custom)
        settings.reset_models_dir()
        self.assertEqual(settings.models_dir(), settings.DEFAULT_MODELS_DIR)
        self.assertFalse(settings.is_custom_models_dir())

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            settings.set_models_dir("")

    def test_drive_only_rejected(self):
        with self.assertRaises(ValueError):
            settings.set_models_dir("C:")

    def test_disk_info(self):
        info = settings.disk_info(self.custom)
        self.assertGreater(info["total_gb"], 0)
        self.assertGreaterEqual(info["free_gb"], 0)
        self.assertEqual(set(info), {"total_gb", "free_gb", "used_gb"})

    def test_available_drives(self):
        drives = settings.available_drives()
        self.assertGreaterEqual(len(drives), 1)
        for d in drives:
            self.assertEqual(set(d), {"path", "free_gb", "total_gb"})
            self.assertRegex(d["path"], r"^[A-Z]:\\$")
            self.assertGreaterEqual(d["free_gb"], 0)


# ---------------------------------------------------------------------------
# everything still compiles (no import side effects)
# ---------------------------------------------------------------------------
class TestCompile(unittest.TestCase):
    MODULES = ["fit", "gguf_backend", "settings", "server", "cli",
               "engine", "hf_hub", "hardware"]

    def test_all_compile(self):
        for mod in self.MODULES:
            path = os.path.join(REPO, mod + ".py")
            with self.subTest(module=mod):
                py_compile.compile(path, doraise=True)


if __name__ == "__main__":
    unittest.main()
