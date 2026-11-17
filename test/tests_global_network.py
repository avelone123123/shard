import asyncio
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from fastapi.testclient import TestClient
from shard_engine import net_discovery, net_identity, net_strategy, net_tunnel, server, training
from shard_engine.distributed import CLUSTER, SCHEDULER, capabilities, pipeline_coordinator, pipeline_proto, pipeline_worker, planner
def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p
def _build_test_model(target_dir):
    import torch
    from safetensors.torch import save_file
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from transformers import PreTrainedTokenizerFast, Qwen2Config, Qwen2ForCausalLM
    os.makedirs(target_dir, exist_ok=True)
    vocab = {"<unk>": 0, "<|endoftext|>": 1, "hello": 2, "shard": 3, "network": 4, "global": 5, "cluster": 6}
    tok_obj = Tokenizer(WordLevel(vocab=vocab, unk_token="<unk>"))
    tok_obj.pre_tokenizer = Whitespace()
    fast_tok = PreTrainedTokenizerFast(tokenizer_object=tok_obj, unk_token="<unk>", eos_token="<|endoftext|>", pad_token="<|endoftext|>")
    fast_tok.save_pretrained(target_dir)
    torch.manual_seed(42)
    cfg = Qwen2Config(vocab_size=len(vocab), hidden_size=32, intermediate_size=64, num_hidden_layers=4, num_attention_heads=4, num_key_value_heads=2, max_position_embeddings=128, tie_word_embeddings=False, eos_token_id=1, pad_token_id=1)
    cfg.save_pretrained(target_dir)
    mdl = Qwen2ForCausalLM(cfg).eval()
    sd = {k: v.detach().cpu().contiguous() for k, v in mdl.state_dict().items()}
    shard1, shard2, weight_map = {}, {}, {}
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
        json.dump({"metadata": {"total_size": sum(t.numel() * t.element_size() for t in sd.values())}, "weight_map": weight_map}, f)
class TestGlobalNetworkSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import uvicorn
        os.environ["SHARD_ALLOW_INSECURE_LOCAL"] = "1"
        cls.tmp_root = tempfile.mkdtemp(prefix="shard_global_net_")
        cls.disc_port = _free_port()
        cls.disc_store = net_discovery.DiscoveryStore()
        cls.disc_app = net_discovery.create_discovery_app(store=cls.disc_store)
        cfg = uvicorn.Config(cls.disc_app, host="127.0.0.1", port=cls.disc_port, log_level="warning")
        cls.disc_server = uvicorn.Server(cfg)
        cls.disc_th = threading.Thread(target=cls.disc_server.run, daemon=True)
        cls.disc_th.start()
        time.sleep(0.5)
        cls.disc_url = f"http://127.0.0.1:{cls.disc_port}"
        os.environ["SHARD_DISCOVERY_URL"] = cls.disc_url
        cls.model_dir = os.path.join(cls.tmp_root, "test_qwen_model")
        _build_test_model(cls.model_dir)
    @classmethod
    def tearDownClass(cls):
        os.environ.pop("SHARD_ALLOW_INSECURE_LOCAL", None)
        os.environ.pop("SHARD_DISCOVERY_URL", None)
        try:
            cls.disc_server.should_exit = True
        except Exception:
            pass
        shutil.rmtree(cls.tmp_root, ignore_errors=True)
    def setUp(self):
        CLUSTER.stop_cluster()
        CLUSTER.stop_local_worker()
        self.node_a_dir = tempfile.mkdtemp(prefix="node_a_")
        self.node_b_dir = tempfile.mkdtemp(prefix="node_b_")
        self.ident_a = net_identity.NodeIdentity(identity_path=os.path.join(self.node_a_dir, "identity.json"))
        self.ident_b = net_identity.NodeIdentity(identity_path=os.path.join(self.node_b_dir, "identity.json"))
        self.client = TestClient(server.app)
        self.owner_hdr = {"X-Shard-Owner-Token": training.get_owner_token()}
    def tearDown(self):
        CLUSTER.stop_cluster()
        CLUSTER.stop_local_worker()
        shutil.rmtree(self.node_a_dir, ignore_errors=True)
        shutil.rmtree(self.node_b_dir, ignore_errors=True)
    def test_a_automatic_network_registration(self):
        dc_a = net_discovery.DiscoveryClient(urls=[self.disc_url])
        listing_a = {
            "node_id": self.ident_a.node_id,
            "pubkey": self.ident_a.pubkey_b64,
            "name": "Node-Alpha",
            "role": "server",
            "visibility": "public",
            "access_mode": "open",
            "active_model": "Qwen/Test-Model",
            "vram_gb": 12.0,
            "aggregate_vram_mb": 12288,
            "accepts_workers": True,
            "cluster_state": "READY",
            "direct_endpoints": ["127.0.0.1:8000"],
        }
        res_a = dc_a.register_server(self.ident_a, listing_a, ttl_s=30)
        self.assertEqual(res_a.get("status"), "ok")
        dc_b = net_discovery.DiscoveryClient(urls=[self.disc_url])
        listing_b = {
            "node_id": self.ident_b.node_id,
            "pubkey": self.ident_b.pubkey_b64,
            "name": "Node-Beta",
            "role": "server",
            "visibility": "public",
            "access_mode": "open",
            "active_model": "Qwen/Test-Model",
            "vram_gb": 8.0,
            "aggregate_vram_mb": 8192,
            "accepts_workers": True,
            "cluster_state": "WAITING_WORKERS",
            "direct_endpoints": ["127.0.0.1:8002"],
        }
        res_b = dc_b.register_server(self.ident_b, listing_b, ttl_s=30)
        self.assertEqual(res_b.get("status"), "ok")
        fetched = dc_b.fetch_servers()
