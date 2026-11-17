import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from fastapi.testclient import TestClient
from shard_engine import gguf_backend, hardware, net_identity, net_tunnel, server
from shard_engine.distributed import CLUSTER, SCHEDULER, capabilities, pipeline_coordinator, pipeline_proto, pipeline_worker, planner
def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p
def _build_sharded_qwen2_model(target_dir):
    import torch
    from safetensors.torch import save_file
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from transformers import PreTrainedTokenizerFast, Qwen2Config, Qwen2ForCausalLM
    os.makedirs(target_dir, exist_ok=True)
    vocab = {"<unk>": 0, "<|endoftext|>": 1, "hello": 2, "world": 3, "shard": 4, "cluster": 5, "ai": 6, "distributed": 7}
    tok_obj = Tokenizer(WordLevel(vocab=vocab, unk_token="<unk>"))
    tok_obj.pre_tokenizer = Whitespace()
    fast_tok = PreTrainedTokenizerFast(tokenizer_object=tok_obj, unk_token="<unk>", eos_token="<|endoftext|>", pad_token="<|endoftext|>")
    fast_tok.save_pretrained(target_dir)
    torch.manual_seed(42)
    cfg = Qwen2Config(
        vocab_size=len(vocab),
        hidden_size=32,
        intermediate_size=64,
        num_hidden_layers=4,
        num_attention_heads=4,
        num_key_value_heads=2,
        max_position_embeddings=128,
        tie_word_embeddings=False,
        eos_token_id=1,
        pad_token_id=1,
    )
    cfg.save_pretrained(target_dir)
    mdl = Qwen2ForCausalLM(cfg).eval()
    sd = {k: v.detach().cpu().contiguous() for k, v in mdl.state_dict().items()}
    shard1 = {}
    shard2 = {}
    weight_map = {}
    for k, v in sd.items():
        if k.startswith("model.embed_tokens") or k.startswith("model.layers.0.") or k.startswith("model.layers.1."):
            shard1[k] = v
            weight_map[k] = "model-00001-of-00002.safetensors"
        else:
            shard2[k] = v
            weight_map[k] = "model-00002-of-00002.safetensors"
    save_file(shard1, os.path.join(target_dir, "model-00001-of-00002.safetensors"))
    save_file(shard2, os.path.join(target_dir, "model-00002-of-00002.safetensors"))
    with open(os.path.join(target_dir, "model.safetensors.index.json"), "w", encoding="utf-8") as f:
        json.dump({"metadata": {"total_size": 0}, "weight_map": weight_map}, f)
    return mdl, fast_tok
class TestDistributedUnit(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="shard_dist_unit_")
        self.orig_data_dir = os.environ.get("SHARD_DATA_DIR")
        os.environ["SHARD_DATA_DIR"] = self.tmp_dir
        CLUSTER.stop_local_worker()
        CLUSTER.stop_cluster()
        SCHEDULER.configure(max_concurrent=4, max_queue_size=32, default_rpm=200)
        server._PAIR_FAIL_LIMITER._hits.clear()
        server._PAIR_TOTAL_LIMITER._hits.clear()
    def tearDown(self):
        CLUSTER.stop_local_worker()
        CLUSTER.stop_cluster()
        if self.orig_data_dir is not None:
            os.environ["SHARD_DATA_DIR"] = self.orig_data_dir
        else:
            os.environ.pop("SHARD_DATA_DIR", None)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
    def test_hardware_discovery_and_quota_enforcement(self):
        hw = hardware.get_hardware_profile(force_refresh=True)
        self.assertIn("cpu_model", hw)
        self.assertIn("backends", hw)
        self.assertGreaterEqual(hw["cpu_cores"], 1)
        fake_hw = {"vram_total_mb": 8192, "vram_free_mb": 7800, "ram_total_mb": 32768, "ram_avail_mb": 28000, "cpu_cores": 16}
        eff = hardware.effective_usable_resources(fake_hw, quota={"vram_pct": 50, "max_ram_gb": 10, "max_threads": 6})
        self.assertEqual(eff["usable_vram_mb"], 4096)
