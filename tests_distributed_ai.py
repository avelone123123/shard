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
        self.assertEqual(eff["usable_ram_mb"], 10240)
        self.assertEqual(eff["usable_threads"], 6)
    def test_planner_weak_coordinator_and_unsupported_arch(self):
        spec_bad = planner.inspect_model_architecture("custom/mamba-3b", fetch=False)
        self.assertFalse(spec_bad["supported_for_torch_pipeline"])
        spec_qwen = {
            "model_id": "Qwen/Qwen2.5-14B",
            "model_type": "qwen2",
            "params_b": 14.0,
            "num_layers": 48,
            "hidden_size": 5120,
            "kv_cache_mb_at_ctx": 512.0,
            "ctx_tokens": 4096,
            "gguf_weight_mb": 9000.0,
            "fp16_weight_mb": 28000.0,
            "tie_word_embeddings": False,
            "supported_for_torch_pipeline": True,
            "has_safetensors": True,
            "weight_map": {},
        }
        nodes = [
            {"node_id": "coordinator", "name": "WeakHost", "role": "coordinator", "hw": {"vram_total_mb": 4096, "vram_free_mb": 3500, "ram_total_mb": 16384, "ram_avail_mb": 8192, "cpu_cores": 4}, "quota": {"max_vram_mb": 0, "max_ram_mb": 0}, "backends": {"cuda": False, "llama_offload_devices": []}, "rtt_ms": 0.1, "bandwidth_mbps": 10000},
            {"node_id": "wrk-1", "name": "WorkerA", "role": "worker", "hw": {"vram_total_mb": 8192, "vram_free_mb": 7800, "ram_total_mb": 32768, "ram_avail_mb": 24000, "cpu_cores": 8}, "quota": {"vram_pct": 85}, "backends": {"cuda": True}, "rpc_endpoint": "10.0.0.2:50052", "rtt_ms": 1.2, "bandwidth_mbps": 1000},
            {"node_id": "wrk-2", "name": "WorkerB", "role": "worker", "hw": {"vram_total_mb": 8192, "vram_free_mb": 7800, "ram_total_mb": 32768, "ram_avail_mb": 24000, "cpu_cores": 8}, "quota": {"vram_pct": 85}, "backends": {"cuda": True}, "rpc_endpoint": "10.0.0.3:50052", "rtt_ms": 1.5, "bandwidth_mbps": 1000},
        ]
        plan = planner.plan_cluster_partition(spec_qwen, nodes, strategy="gguf_rpc_layer_split", ctx_tokens=4096, parallel_slots=2)
        self.assertTrue(plan["feasible"], plan.get("reason"))
        self.assertEqual(plan["total_layers"], 48)
        self.assertEqual(sum(s["layer_count"] for s in plan["stages"]), 48)
        self.assertEqual(len(plan["tensor_split_arg"].split(",")), 2)
        self.assertEqual(plan["rpc_arg"], "10.0.0.2:50052,10.0.0.3:50052")
        insufficient = planner.plan_cluster_partition(spec_qwen, nodes[:2], strategy="gguf_rpc_layer_split", ctx_tokens=4096, parallel_slots=2)
        self.assertFalse(insufficient["feasible"])
    def test_worker_consent_auth_and_api_lifecycle(self):
        client = TestClient(server.app, client=("127.0.0.1", 50000))
        r_create = client.post("/api/cluster/create", json={"model_id": "Qwen/Qwen2.5-Coder-1.5B-Instruct", "name": "TestCluster", "auto_deploy": False, "fetch_model_meta": False})
        self.assertEqual(r_create.status_code, 200)
        cls_data = r_create.json()["cluster"]
        self.assertEqual(cls_data["state"], "FORMING")
        code = cls_data["pairing_code"]
        self.assertTrue(code)
        r_prev = client.get("/api/cluster/preview")
        self.assertEqual(r_prev.status_code, 200)
        self.assertTrue(r_prev.json()["consent_required"])
        r_noconsent = client.post("/api/cluster/workers/register", json={"worker_id": "w1", "consented": False, "auth_code": code})
        self.assertEqual(r_noconsent.status_code, 403)
        r_badcode = client.post("/api/cluster/workers/register", json={"worker_id": "w1", "consented": True, "auth_code": "AAAA-BBBB"})
        self.assertEqual(r_badcode.status_code, 403)
        r_ok = client.post("/api/cluster/workers/register", json={
            "worker_id": "w1",
            "name": "Node-1",
            "consented": True,
            "auth_code": code,
            "hw": {"vram_total_mb": 8192, "vram_free_mb": 7500, "ram_total_mb": 16384, "ram_avail_mb": 12000, "cpu_cores": 8},
            "quota": {"vram_pct": 70, "max_threads": 4},
            "rpc_port": 50052,
        })
        self.assertEqual(r_ok.status_code, 200)
        wtok = r_ok.json()["worker_token"]
        self.assertTrue(wtok.startswith("sk-shard-wrk-"))
        r_hb_bad = client.post("/api/cluster/workers/heartbeat", json={"worker_id": "w1", "worker_token": "forged"})
        self.assertEqual(r_hb_bad.status_code, 401)
        r_hb_ok = client.post("/api/cluster/workers/heartbeat", json={"worker_id": "w1", "worker_token": wtok})
        self.assertEqual(r_hb_ok.status_code, 200)
        r_leave = client.post("/api/cluster/workers/leave", json={"worker_id": "w1", "worker_token": wtok})
        self.assertEqual(r_leave.status_code, 200)
        r_st = client.get("/api/cluster/status")
        self.assertEqual(len(r_st.json()["workers"]), 0)
    def test_scheduler_queue_rate_limit_and_cancel(self):
        from shard_engine.distributed.scheduler import QueueFullError, RateLimitError, RequestScheduler
        sched = RequestScheduler(max_concurrent=1, max_queue_size=1, default_rpm=2)
        rid1, _ = sched.acquire_slot(client_key="u1")
        sched.finish_slot(rid1, ok=True, ttft_ms=10.0, tps=20.0)
        rid2, _ = sched.acquire_slot(client_key="u1")
        with self.assertRaises(RateLimitError):
            sched.acquire_slot(client_key="u1")
        sched.finish_slot(rid2, ok=True)
        rid3, ev3 = sched.acquire_slot(client_key="u2")
        self.assertTrue(sched.cancel_request(rid3))
        self.assertTrue(ev3.is_set())
        sched.finish_slot(rid3, ok=False, cancelled=True)
        s2 = RequestScheduler(max_concurrent=1, max_queue_size=0, default_rpm=100)
        r_hold, _ = s2.acquire_slot(client_key="a")
        s2.max_queue_size = 0
        with self.assertRaises(QueueFullError):
            s2.acquire_slot(client_key="b")
        s2.finish_slot(r_hold, ok=True)
class TestRealDistributedIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="shard_dist_int_")
        self.orig_data_dir = os.environ.get("SHARD_DATA_DIR")
        os.environ["SHARD_DATA_DIR"] = self.tmp_dir
        CLUSTER.stop_local_worker()
        CLUSTER.stop_cluster()
    def tearDown(self):
        CLUSTER.stop_local_worker()
        CLUSTER.stop_cluster()
        if self.orig_data_dir is not None:
            os.environ["SHARD_DATA_DIR"] = self.orig_data_dir
        else:
            os.environ.pop("SHARD_DATA_DIR", None)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
    def test_real_multiprocess_pytorch_pipeline_parallelism(self):
        import importlib.util
        if any(importlib.util.find_spec(m) is None for m in ("torch", "transformers", "safetensors")):
            self.skipTest("torch/transformers/safetensors not installed")
        import torch
        model_dir = os.path.join(self.tmp_dir, "tiny_qwen2_sharded")
        ref_model, tok = _build_sharded_qwen2_model(model_dir)
        p1 = _free_port()
        p2 = _free_port()
        t1 = "sk-shard-wrk-proc1"
        t2 = "sk-shard-wrk-proc2"
        worker_script = (
            "import sys, time\n"
            "from shard_engine.distributed.pipeline_worker import PipelineWorkerServer\n"
            "srv = PipelineWorkerServer(host='127.0.0.1', port=int(sys.argv[1]), auth_token=sys.argv[2], max_threads=2, allow_cuda=False)\n"
            "srv.start()\n"
            "print('READY', flush=True)\n"
            "while True:\n"
            "    time.sleep(0.5)\n"
        )
        proc1 = subprocess.Popen([sys.executable, "-c", worker_script, str(p1), t1], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        proc2 = subprocess.Popen([sys.executable, "-c", worker_script, str(p2), t2], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc1.stdout.readline().strip(), "READY")
            self.assertEqual(proc2.stdout.readline().strip(), "READY")
            spec = planner.inspect_model_architecture(model_dir, ctx_tokens=128, fetch=False)
            self.assertTrue(spec["supported_for_torch_pipeline"])
            self.assertTrue(spec["has_safetensors"])
            nodes = [
                {"node_id": "coordinator", "name": "WeakCoord", "role": "coordinator", "hw": {"vram_total_mb": 0, "vram_free_mb": 0, "ram_total_mb": 4096, "ram_avail_mb": 2048, "cpu_cores": 2}, "quota": {"max_vram_mb": 0, "max_ram_mb": 0}, "backends": {"torch_pipeline": True}, "rtt_ms": 0.1, "bandwidth_mbps": 10000},
                {"node_id": "wrk-1", "name": "ProcWorker1", "role": "worker", "hw": {"vram_total_mb": 0, "vram_free_mb": 0, "ram_total_mb": 8192, "ram_avail_mb": 6000, "cpu_cores": 4}, "quota": {"max_ram_gb": 4}, "backends": {"torch_pipeline": True}, "pipeline_endpoint": f"127.0.0.1:{p1}", "rtt_ms": 0.5, "bandwidth_mbps": 1000},
                {"node_id": "wrk-2", "name": "ProcWorker2", "role": "worker", "hw": {"vram_total_mb": 0, "vram_free_mb": 0, "ram_total_mb": 8192, "ram_avail_mb": 6000, "cpu_cores": 4}, "quota": {"max_ram_gb": 4}, "backends": {"torch_pipeline": True}, "pipeline_endpoint": f"127.0.0.1:{p2}", "rtt_ms": 0.5, "bandwidth_mbps": 1000},
            ]
            plan = planner.plan_cluster_partition(spec, nodes, strategy="torch_pipeline_parallel", ctx_tokens=128, parallel_slots=1)
            self.assertTrue(plan["feasible"], plan.get("reason"))
            self.assertEqual(len(plan["stages"]), 2)
            self.assertEqual(plan["stages"][0]["shard_files"], ["model-00001-of-00002.safetensors"])
            self.assertEqual(plan["stages"][1]["shard_files"], ["model-00002-of-00002.safetensors"])
            coord = pipeline_coordinator.PipelineCoordinator(model_dir, plan, worker_tokens={"wrk-1": t1, "wrk-2": t2}, cluster_id="test-pipe")
            load_res = coord.start()
            self.assertEqual(load_res["status"], "ready")
            self.assertEqual(len(load_res["stages"]), 2)
            prompt_ids = tok("hello world shard", return_tensors="pt")["input_ids"]
            with torch.inference_mode():
                ref_logits = ref_model(input_ids=prompt_ids).logits[:, -1:, :]
            dist_logits, hop_stats = coord.forward_logits(prompt_ids, request_id="eq-test", use_cache=True)
            self.assertTrue(torch.allclose(ref_logits, dist_logits, atol=1e-4), f"Max diff: {(ref_logits - dist_logits).abs().max().item()}")
            self.assertGreater(hop_stats["transferred_bytes"], 0)
            coord.release_request("eq-test")
            events = list(coord.generate_stream([{"role": "user", "content": "hello shard"}], max_tokens=4, temperature=0.0))
            done_ev = [e for e in events if e.get("type") == "done"]
            self.assertEqual(len(done_ev), 1)
            self.assertGreater(done_ev[0]["telemetry"]["transferred_bytes"], 0)
            self.assertGreater(done_ev[0]["usage"]["completion_tokens"], 0)
            coord.stop()
        finally:
            proc1.terminate()
            proc2.terminate()
            proc1.wait(timeout=5)
            proc2.wait(timeout=5)
            for p in (proc1, proc2):
                for strm in (p.stdout, p.stderr):
                    if strm is not None:
                        try:
                            strm.close()
                        except Exception:
                            pass
    def test_real_multiprocess_gguf_rpc_cluster_and_api(self):
        rpc_exe = None
        from shard_engine import lan
        rpc_exe = lan.find_rpc_server()
        srv_exe = gguf_backend.find_llama_server()
        loc_gguf = gguf_backend.find_local_gguf("mradermacher/gpt2-medium-i1-GGUF")
        if not (rpc_exe and srv_exe and loc_gguf):
            self.skipTest("llama-server / ggml-rpc-server / cached gpt2-medium GGUF not present")
        p1 = _free_port()
        p2 = _free_port()
        w1 = subprocess.Popen([rpc_exe, "-H", "127.0.0.1", "-p", str(p1), "-t", "2"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        w2 = subprocess.Popen([rpc_exe, "-H", "127.0.0.1", "-p", str(p2), "-t", "2"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(0.6)
            st = CLUSTER.create_cluster(
                name="RealGGUFCluster",
                model_id=loc_gguf["path"],
                strategy="gguf_rpc_layer_split",
                ctx_tokens=512,
                parallel_slots=2,
                open_net=False,
                coordinator_contributes=False,
                auto_deploy=False,
                fetch_model_meta=False,
            )
            code = st["pairing_code"]
            hw_w = {"vram_total_mb": 0, "vram_free_mb": 0, "ram_total_mb": 8192, "ram_avail_mb": 6144, "cpu_cores": 4}
            CLUSTER.register_worker(worker_id="rpc-w1", name="RPC-1", hw=hw_w, quota={"max_ram_gb": 3, "max_threads": 2}, consented=True, auth_code=code, rpc_port=p1, rpc_host="127.0.0.1")
            CLUSTER.register_worker(worker_id="rpc-w2", name="RPC-2", hw=hw_w, quota={"max_ram_gb": 3, "max_threads": 2}, consented=True, auth_code=code, rpc_port=p2, rpc_host="127.0.0.1")
            dep = CLUSTER.deploy_cluster(verify=True)
            self.assertEqual(dep["state"], "READY")
            self.assertTrue(CLUSTER.is_ready())
            self.assertEqual(len(dep["plan"]["stages"]), 2)
            client = TestClient(server.app, client=("127.0.0.1", 50000))
            r_openai = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "Say ok"}], "max_tokens": 8, "temperature": 0.0})
            self.assertEqual(r_openai.status_code, 200)
            body_oa = r_openai.json()
            self.assertIn("choices", body_oa)
            self.assertGreater(body_oa["usage"]["completion_tokens"], 0)
            r_anth = client.post("/v1/messages", json={"messages": [{"role": "user", "content": "Say ok"}], "max_tokens": 8, "temperature": 0.0})
            self.assertEqual(r_anth.status_code, 200)
            self.assertIn("content", r_anth.json())
            r_tel = client.get("/api/cluster/telemetry")
            self.assertEqual(r_tel.status_code, 200)
            tel = r_tel.json()["telemetry"]
            self.assertGreaterEqual(tel["completed_requests"], 2)
            self.assertGreater(tel["total_tokens_generated"], 0)
        finally:
            CLUSTER.stop_cluster()
            w1.terminate()
            w2.terminate()
            w1.wait(timeout=5)
            w2.wait(timeout=5)
    def test_reverse_encrypted_tunnel_worker_proxy(self):
        async def _run():
            id_host = net_identity.NodeIdentity(os.path.join(self.tmp_dir, "host_id.json"))
            id_wrk = net_identity.NodeIdentity(os.path.join(self.tmp_dir, "wrk_id.json"))
            wtok = "sk-shard-wrk-tunnel-secret"
            pipe_srv = pipeline_worker.PipelineWorkerServer(host="127.0.0.1", port=0, auth_token=wtok)
            w_port = pipe_srv.start()
            from fastapi import FastAPI
            dummy_app = FastAPI()
            @dummy_app.get("/v1/ping")
            def _p():
                return {"status": "ok"}
            ha = net_tunnel.HostTunnelAgent(identity=id_host, asgi_app=dummy_app, p2p_host="127.0.0.1", p2p_port=0, allow_insecure_local=True)
            await ha.start()
            ca = net_tunnel.ClientTunnelAgent(
                identity=id_wrk,
                target_host_id=id_host.node_id,
                mode="direct",
                direct_endpoints=[f"127.0.0.1:{ha.bound_p2p_port}"],
                allow_insecure_local=True,
                worker_allowed_ports=[w_port],
            )
            ok = await ca.connect()
            self.assertTrue(ok)
            try:
                proxy_port = await ha.open_worker_proxy(id_wrk.node_id, w_port)
                self.assertGreater(proxy_port, 0)
                def _probe_via_proxy():
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(5.0)
                    s.connect(("127.0.0.1", proxy_port))
                    try:
                        pipeline_proto.client_auth_handshake(s, wtok)
                        pipeline_proto.send_json_frame(s, pipeline_proto.MSG_PING, {})
                        mtype, _, obj = pipeline_proto.recv_json_frame(s)
                        return mtype, obj
                    finally:
                        s.close()
                mtype, obj = await asyncio.to_thread(_probe_via_proxy)
                self.assertEqual(mtype, pipeline_proto.MSG_PONG)
                self.assertEqual(obj["port"], w_port)
            finally:
                await ca.disconnect()
                await ha.stop()
                pipe_srv.stop()
        asyncio.run(_run())
    def test_concurrent_multi_user_streaming_and_api_keys(self):
        from shard_engine import lan
        rpc_exe = lan.find_rpc_server()
        srv_exe = gguf_backend.find_llama_server()
        loc_gguf = gguf_backend.find_local_gguf("mradermacher/gpt2-medium-i1-GGUF")
        if not (rpc_exe and srv_exe and loc_gguf):
            self.skipTest("llama-server / ggml-rpc-server / cached gpt2-medium GGUF not present")
        p1, p2 = _free_port(), _free_port()
        w1 = subprocess.Popen([rpc_exe, "-H", "127.0.0.1", "-p", str(p1), "-t", "2"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        w2 = subprocess.Popen([rpc_exe, "-H", "127.0.0.1", "-p", str(p2), "-t", "2"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(0.5)
            st = CLUSTER.create_cluster(
                name="ConcurrentCluster",
                model_id=loc_gguf["path"],
                strategy="gguf_rpc_layer_split",
                ctx_tokens=512,
                parallel_slots=2,
                open_net=True,
                coordinator_contributes=False,
                auto_deploy=False,
                fetch_model_meta=False,
            )
            hw_w = {"vram_total_mb": 0, "vram_free_mb": 0, "ram_total_mb": 8192, "ram_avail_mb": 6144, "cpu_cores": 4}
            CLUSTER.register_worker(worker_id="cw1", name="CW-1", hw=hw_w, quota={"max_ram_gb": 3, "max_threads": 2}, consented=True, rpc_port=p1, rpc_host="127.0.0.1")
            CLUSTER.register_worker(worker_id="cw2", name="CW-2", hw=hw_w, quota={"max_ram_gb": 3, "max_threads": 2}, consented=True, rpc_port=p2, rpc_host="127.0.0.1")
            CLUSTER.deploy_cluster(verify=True)
            remote_bad = TestClient(server.app, client=("192.168.1.50", 51000), headers={"Authorization": "Bearer sk-invalid-key"})
            r_unauth = remote_bad.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
            self.assertEqual(r_unauth.status_code, 401)
            api_key = server.load_keys()[0]
            results = []
            def _run_stream(idx):
                c = TestClient(server.app, client=(f"192.168.1.{60 + idx}", 52000 + idx), headers={"Authorization": f"Bearer {api_key}"})
                with c.stream("POST", "/v1/chat/completions", json={"messages": [{"role": "user", "content": f"Hello {idx}"}], "max_tokens": 6, "stream": True}) as resp:
                    body = "".join(resp.iter_text())
                    results.append((resp.status_code, body))
            threads = [threading.Thread(target=_run_stream, args=(i,)) for i in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
            self.assertEqual(len(results), 4)
            for code, body in results:
                self.assertEqual(code, 200)
                self.assertIn("data: [DONE]", body)
        finally:
            CLUSTER.stop_cluster()
            w1.terminate()
            w2.terminate()
            w1.wait(timeout=5)
            w2.wait(timeout=5)
    def test_worker_disconnect_and_rebalance_recovery(self):
        from shard_engine import lan
        rpc_exe = lan.find_rpc_server()
        srv_exe = gguf_backend.find_llama_server()
        loc_gguf = gguf_backend.find_local_gguf("mradermacher/gpt2-medium-i1-GGUF")
        if not (rpc_exe and srv_exe and loc_gguf):
            self.skipTest("llama-server / ggml-rpc-server / cached gpt2-medium GGUF not present")
        p1, p2, p3 = _free_port(), _free_port(), _free_port()
        w1 = subprocess.Popen([rpc_exe, "-H", "127.0.0.1", "-p", str(p1), "-t", "2"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        w2 = subprocess.Popen([rpc_exe, "-H", "127.0.0.1", "-p", str(p2), "-t", "2"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        w3 = subprocess.Popen([rpc_exe, "-H", "127.0.0.1", "-p", str(p3), "-t", "2"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(0.5)
            st = CLUSTER.create_cluster(
                name="FailoverCluster",
                model_id=loc_gguf["path"],
                strategy="gguf_rpc_layer_split",
                ctx_tokens=512,
                parallel_slots=1,
                open_net=True,
                coordinator_contributes=False,
                auto_deploy=False,
                fetch_model_meta=False,
            )
            hw_w = {"vram_total_mb": 0, "vram_free_mb": 0, "ram_total_mb": 8192, "ram_avail_mb": 6144, "cpu_cores": 4}
            CLUSTER.register_worker(worker_id="fw1", name="FW-1", hw=hw_w, quota={"max_ram_gb": 3, "max_threads": 2}, consented=True, rpc_port=p1, rpc_host="127.0.0.1")
            CLUSTER.register_worker(worker_id="fw2", name="FW-2", hw=hw_w, quota={"max_ram_gb": 3, "max_threads": 2}, consented=True, rpc_port=p2, rpc_host="127.0.0.1")
            r3 = CLUSTER.register_worker(worker_id="fw3", name="FW-3", hw=hw_w, quota={"max_ram_gb": 3, "max_threads": 2}, consented=True, rpc_port=p3, rpc_host="127.0.0.1")
            dep1 = CLUSTER.deploy_cluster(verify=True)
            self.assertEqual(len(dep1["plan"]["stages"]), 3)
            CLUSTER.leave_worker("fw3", r3["worker_token"])
            self.assertEqual(CLUSTER.state, "DEGRADED")
            dep2 = CLUSTER.deploy_cluster(verify=True)
            self.assertEqual(dep2["state"], "READY")
            self.assertEqual(len(dep2["plan"]["stages"]), 2)
        finally:
            CLUSTER.stop_cluster()
            for wp in (w1, w2, w3):
                wp.terminate()
                wp.wait(timeout=5)
