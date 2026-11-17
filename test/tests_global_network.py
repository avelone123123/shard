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
        self.assertEqual(fetched.get("status"), "ok")
        self.assertGreaterEqual(fetched.get("count"), 2)
        node_ids = [s["node_id"] for s in fetched["servers"]]
        self.assertIn(self.ident_a.node_id, node_ids)
        self.assertIn(self.ident_b.node_id, node_ids)
    def test_b_global_server_discovery_and_catalog_filtering(self):
        dc = net_discovery.DiscoveryClient(urls=[self.disc_url])
        listing = {
            "node_id": self.ident_a.node_id,
            "pubkey": self.ident_a.pubkey_b64,
            "name": "Alpha-DeepSeek-Cluster",
            "role": "server",
            "visibility": "public",
            "access_mode": "open",
            "active_model": "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B",
            "vram_gb": 24.0,
            "aggregate_vram_mb": 24576,
            "worker_count": 3,
            "accepts_workers": True,
            "cluster_state": "READY",
            "direct_endpoints": ["127.0.0.1:9090"],
        }
        dc.register_server(self.ident_a, listing, ttl_s=30)
        resp_q = self.client.get("/api/catalog/servers?q=DeepSeek", headers=self.owner_hdr)
        self.assertEqual(resp_q.status_code, 200)
        servers_q = resp_q.json().get("servers") or []
        self.assertTrue(any("deepseek" in (s.get("listing", {}).get("active_model", "").lower()) for s in servers_q))
        resp_vram = self.client.get("/api/catalog/servers?min_vram=16", headers=self.owner_hdr)
        self.assertEqual(resp_vram.status_code, 200)
        servers_vram = resp_vram.json().get("servers") or []
        self.assertTrue(all(float(s.get("listing", {}).get("vram_gb") or 0) >= 16 for s in servers_vram))
        resp_details = self.client.get(f"/api/catalog/servers/{self.ident_a.node_id}", headers=self.owner_hdr)
        self.assertEqual(resp_details.status_code, 200)
        data = resp_details.json()
        self.assertEqual(data.get("node_id"), self.ident_a.node_id)
        self.assertEqual(data.get("public", {}).get("listing", {}).get("name"), "Alpha-DeepSeek-Cluster")
    def test_c_one_click_worker_join_prejoin_and_consent(self):
        dc = net_discovery.DiscoveryClient(urls=[self.disc_url])
        listing = {
            "node_id": self.ident_a.node_id,
            "pubkey": self.ident_a.pubkey_b64,
            "name": "Cluster-Coordinator",
            "role": "server",
            "visibility": "public",
            "access_mode": "open",
            "active_model": "Qwen/Qwen2.5-Coder-7B-Instruct",
            "vram_gb": 16.0,
            "aggregate_vram_mb": 16384,
            "worker_count": 1,
            "accepts_workers": True,
            "cluster_state": "WAITING_WORKERS",
            "direct_endpoints": ["127.0.0.1:8000"],
        }
        dc.register_server(self.ident_a, listing, ttl_s=30)
        resp_pj = self.client.get(f"/api/catalog/prejoin/{self.ident_a.node_id}", headers=self.owner_hdr)
        self.assertEqual(resp_pj.status_code, 200)
        pj = resp_pj.json()
        self.assertEqual(pj.get("target_model"), "Qwen/Qwen2.5-Coder-7B-Instruct")
        self.assertTrue(pj.get("compatible"))
        self.assertIn("gpu_model", pj.get("local_hardware", {}))
        self.assertIn("vram_pct", pj.get("recommendations", {}))
        resp_no_consent = self.client.post("/api/catalog/join", json={"target": self.ident_a.node_id, "mode": "worker", "consent": False}, headers=self.owner_hdr)
        self.assertEqual(resp_no_consent.status_code, 403)
        self.assertEqual(resp_no_consent.json().get("code"), "CONSENT_REQUIRED")
    def test_d_real_distributed_inference_pipeline_parallelism(self):
        import torch
        p1 = _free_port()
        srv = pipeline_worker.PipelineWorkerServer(host="127.0.0.1", port=p1, auth_token="tok_w1", max_threads=2, allow_cuda=False)
        pipe_port = srv.start()
        plan = {
            "feasible": True,
            "valid": True,
            "model_id": self.model_dir,
            "total_layers": 4,
            "strategy": "torch_pipeline_parallel",
            "stages": [
                {"stage_idx": 0, "node_id": "coordinator", "worker_id": "coordinator", "role": "coordinator", "layer_start": 0, "layer_end": 2, "has_embed": True, "has_lm_head": False, "shard_files": ["model-00001-of-00002.safetensors"]},
                {"stage_idx": 1, "node_id": "worker-1", "worker_id": "worker-1", "role": "worker", "endpoint": f"127.0.0.1:{pipe_port}", "layer_start": 2, "layer_end": 4, "has_embed": False, "has_lm_head": True, "shard_files": ["model-00002-of-00002.safetensors"]},
            ],
        }
        coord = pipeline_coordinator.PipelineCoordinator(self.model_dir, plan, worker_tokens={"worker-1": "tok_w1"}, cluster_id="test-pipe")
        load_res = coord.start()
        self.assertEqual(load_res.get("status"), "ready")
        events = list(coord.generate_stream([{"role": "user", "content": "hello shard"}], max_tokens=4, temperature=0.0))
        done_ev = [e for e in events if e.get("type") == "done"]
        self.assertEqual(len(done_ev), 1)
        self.assertGreater(done_ev[0]["usage"]["completion_tokens"], 0)
        coord.stop()
        srv.stop()
    def test_e_multi_user_concurrent_streaming_and_scheduling(self):
        def _factory(label):
            def _gen(cancel_ev):
                for w in [label, "distributed", "inference", "ok"]:
                    if cancel_ev.is_set():
                        break
                    yield {"type": "content", "token": w}
                yield {"type": "done", "token": "", "done": True}
            return _gen
        async def _client(uid):
            items = []
            async for ch in SCHEDULER.stream_async(_factory(f"u{uid}"), client_key=f"user-{uid}"):
                items.append(ch)
            return items
        async def _run_concurrent():
            t1 = asyncio.create_task(_client(1))
            t2 = asyncio.create_task(_client(2))
            t3 = asyncio.create_task(_client(3))
            return await asyncio.gather(t1, t2, t3)
        res = asyncio.run(_run_concurrent())
        self.assertEqual(len(res), 3)
        for r in res:
            self.assertGreaterEqual(len(r), 2)
    def test_f_worker_disconnect_detection_and_degradation(self):
        CLUSTER.create_cluster(name="FailoverCluster", model_id=self.model_dir, strategy="torch", open_net=True, coordinator_contributes=False)
        w_res = CLUSTER.register_worker(
            worker_id="wrk-failover-1",
            name="FlakyWorker",
            hw={"usable_vram_mb": 4096, "usable_ram_mb": 8192, "cpu_threads": 4, "backends": {"torch_pipeline": True}},
            backends={"torch_pipeline_parallel": True},
            quota={"vram_pct": 70},
            consented=True,
            pipeline_host="127.0.0.1",
            pipeline_port=_free_port(),
            transport="lan",
        )
        self.assertEqual(w_res.get("status"), "ok")
        self.assertIn("wrk-failover-1", CLUSTER.workers)
        CLUSTER.workers["wrk-failover-1"]["last_heartbeat"] = time.time() - 30.0
        CLUSTER.workers["wrk-failover-1"]["assigned_stage"] = 1
        CLUSTER.state = "READY"
        CLUSTER._rebalance_needed.set()
        time.sleep(0.3)
        self.assertEqual(CLUSTER.state, "DEGRADED")
    def test_g_network_transport_and_relay_fallback(self):
        nm = net_strategy.get_network_manager()
        diag = nm.diagnostics(probe_public=False)
        self.assertIn("lan", diag)
        self.assertIn("wireguard", diag)
        status = nm.status()
        self.assertIn("node_id", status)
