import asyncio
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
from fastapi.testclient import TestClient
from shard_engine import server, training
from shard_engine.distributed import CLUSTER, SCHEDULER, pipeline_coordinator, pipeline_proto as proto, pipeline_worker, resilience
def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p
def _build_sharded_qwen2(target_dir, hidden=32, layers=4):
    import torch
    from safetensors.torch import save_file
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from transformers import PreTrainedTokenizerFast, Qwen2Config, Qwen2ForCausalLM
    os.makedirs(target_dir, exist_ok=True)
    vocab = {"<unk>": 0, "<|endoftext|>": 1, "hello": 2, "shard": 3, "resilient": 4, "worker": 5, "alpha": 6, "beta": 7}
    tok_obj = Tokenizer(WordLevel(vocab=vocab, unk_token="<unk>"))
    tok_obj.pre_tokenizer = Whitespace()
    fast_tok = PreTrainedTokenizerFast(tokenizer_object=tok_obj, unk_token="<unk>", eos_token="<|endoftext|>", pad_token="<|endoftext|>")
    fast_tok.save_pretrained(target_dir)
    torch.manual_seed(7)
    cfg = Qwen2Config(vocab_size=len(vocab), hidden_size=hidden, intermediate_size=hidden * 2, num_hidden_layers=layers, num_attention_heads=4, num_key_value_heads=2, max_position_embeddings=128, tie_word_embeddings=False, eos_token_id=1, pad_token_id=1)
    cfg.save_pretrained(target_dir)
    mdl = Qwen2ForCausalLM(cfg).eval()
    with torch.no_grad():
        mdl.lm_head.weight[0].zero_()
        mdl.lm_head.weight[1].zero_()
        mdl.lm_head.weight[3].copy_(-mdl.lm_head.weight[2])
        mdl.lm_head.weight[5].copy_(-mdl.lm_head.weight[4])
    sd = {k: v.detach().cpu().contiguous() for k, v in mdl.state_dict().items()}
    s1, s2, wmap = {}, {}, {}
    mid = layers // 2
    for k, v in sd.items():
        shard_two = False
        if k.startswith("lm_head") or k.startswith("model.norm."):
            shard_two = True
        elif k.startswith("model.layers."):
            idx = int(k.split(".")[2])
            shard_two = idx >= mid
        if shard_two:
            s2[k] = v
            wmap[k] = "model-00002-of-00002.safetensors"
        else:
            s1[k] = v
            wmap[k] = "model-00001-of-00002.safetensors"
    save_file(s1, os.path.join(target_dir, "model-00001-of-00002.safetensors"))
    save_file(s2, os.path.join(target_dir, "model-00002-of-00002.safetensors"))
    with open(os.path.join(target_dir, "model.safetensors.index.json"), "w", encoding="utf-8") as f:
        json.dump({"metadata": {"total_size": sum(t.numel() * t.element_size() for t in sd.values())}, "weight_map": wmap}, f)
class _ScriptedPeerServer:
    def __init__(self, behavior="ok", delay_s=0.0, fail_after_forwards=None, auth_token="tok", vocab_size=8):
        self.behavior = behavior
        self.delay_s = float(delay_s)
        self.fail_after_forwards = fail_after_forwards
        self.auth_token = auth_token
        self.vocab_size = vocab_size
        self.forward_count = 0
        self.release_count = 0
        self.stop_ev = threading.Event()
        self.forward_seen = threading.Event()
        self.sock = None
        self.port = None
        self.conns = []
    def start(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        s.listen(16)
        s.settimeout(0.2)
        self.sock = s
        self.port = s.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()
        return self.port
    def stop(self):
        self.stop_ev.set()
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        for c in list(self.conns):
            try:
                c.close()
            except OSError:
                pass
    def _accept(self):
        while not self.stop_ev.is_set():
            try:
                c, _ = self.sock.accept()
                self.conns.append(c)
                threading.Thread(target=self._handle, args=(c,), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break
    def _handle(self, conn):
        import torch
        conn.settimeout(0.5)
        try:
            mtype, _, payload = proto.recv_frame(conn)
            if mtype != proto.MSG_AUTH:
                return
            proto.send_json_frame(conn, proto.MSG_AUTH_OK, {"status": "ok"})
            while not self.stop_ev.is_set():
                try:
                    mtype, _, payload = proto.recv_frame(conn)
                except socket.timeout:
                    continue
                if mtype == proto.MSG_PING:
                    proto.send_json_frame(conn, proto.MSG_PONG, {"status": "ok"})
                elif mtype == proto.MSG_LOAD_STAGE:
                    if self.behavior == "crash_on_load":
                        return
                    if self.behavior == "hang_on_load":
                        self.stop_ev.wait(10.0)
                        return
                    proto.send_json_frame(conn, proto.MSG_STAGE_READY, {"status": "ready"})
                elif mtype == proto.MSG_FORWARD:
                    meta, _ = proto.decode_tensor_payload(payload)
                    self.forward_count += 1
                    self.forward_seen.set()
                    if self.fail_after_forwards is not None and self.forward_count > self.fail_after_forwards:
                        return
                    if self.behavior == "blackhole":
                        self.stop_ev.wait(10.0)
                        return
                    if self.behavior == "stage_error":
                        proto.send_json_frame(conn, proto.MSG_ERROR, {"error": "Injected stage failure"})
                        continue
                    if self.delay_s > 0:
                        self.stop_ev.wait(self.delay_s)
                        if self.stop_ev.is_set():
                            return
                    logits = torch.zeros((1, 1, self.vocab_size), dtype=torch.float32)
                    logits[0, 0, 2 + (self.forward_count % 5)] = 10.0
                    if self.behavior == "stale_seq":
                        out_meta = {"request_id": meta.get("request_id"), "seq": 99999, "execution_id": meta.get("execution_id"), "stage_compute_ms": 1.0}
                    elif self.behavior == "stale_exec":
                        out_meta = {"request_id": meta.get("request_id"), "seq": meta.get("seq"), "execution_id": "exe-stale999", "stage_compute_ms": 1.0}
                    else:
                        out_meta = {"request_id": meta.get("request_id"), "seq": meta.get("seq"), "execution_id": meta.get("execution_id"), "stage_compute_ms": 1.0}
                    proto.send_frame(conn, proto.MSG_FORWARD_RES, proto.encode_tensor_payload(out_meta, logits))
                elif mtype == proto.MSG_RELEASE_KV:
                    self.release_count += 1
                    proto.send_json_frame(conn, proto.MSG_PONG, {"released": True})
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
class TestWorkerResilienceSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["SHARD_ALLOW_INSECURE_LOCAL"] = "1"
        cls.tmp_dir = tempfile.mkdtemp(prefix="shard_resilience_")
        cls.model_dir = os.path.join(cls.tmp_dir, "qwen2_4layer")
        _build_sharded_qwen2(cls.model_dir)
    @classmethod
    def tearDownClass(cls):
        os.environ.pop("SHARD_ALLOW_INSECURE_LOCAL", None)
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)
    def setUp(self):
        CLUSTER.stop_cluster()
        CLUSTER.stop_local_worker()
        resilience.POLICY.reset()
        resilience.METRICS.reset()
        SCHEDULER.configure(max_concurrent=4, max_queue_size=64, default_rpm=120, default_timeout_s=300.0)
        self.client = TestClient(server.app, client=("127.0.0.1", 50000))
        self.owner_hdr = {"X-Shard-Owner-Token": training.get_owner_token()}
        self._servers = []
        self._hb_stops = []
    def tearDown(self):
        for ev in self._hb_stops:
            ev.set()
        for s in self._servers:
            try:
                s.stop()
            except Exception:
                pass
        CLUSTER.stop_cluster()
        CLUSTER.stop_local_worker()
        resilience.POLICY.reset()
    def _start_hb(self, worker_id, token, interval=0.08):
        ev = threading.Event()
        self._hb_stops.append(ev)
        def _loop():
            while not ev.wait(interval):
                CLUSTER.heartbeat_worker(worker_id, token)
        threading.Thread(target=_loop, daemon=True).start()
        return ev
    def _srv_port(self, srv):
        return getattr(srv, "bound_port", None) or getattr(srv, "port", None)
    def _hw(self, ram_mb=8192):
        return {"vram_total_mb": 0, "vram_free_mb": 0, "ram_total_mb": ram_mb, "ram_avail_mb": ram_mb, "cpu_cores": 4, "backends": {"torch_pipeline": True}}
    def _setup_two_worker_cluster(self, s1, s2, coord_contrib=True, ram1=8192, ram2=8192):
        CLUSTER.create_cluster(name="ResilienceCluster", model_id=self.model_dir, strategy="torch_pipeline_parallel", ctx_tokens=128, parallel_slots=2, open_net=True, coordinator_contributes=coord_contrib, auto_deploy=False, fetch_model_meta=False)
        r1 = CLUSTER.register_worker(worker_id="w1", name="W1", hw=self._hw(ram1), quota={"max_ram_gb": ram1 / 1024}, consented=True, pipeline_port=self._srv_port(s1), pipeline_host="127.0.0.1")
        r2 = CLUSTER.register_worker(worker_id="w2", name="W2", hw=self._hw(ram2), quota={"max_ram_gb": ram2 / 1024}, consented=True, pipeline_port=self._srv_port(s2), pipeline_host="127.0.0.1")
        if hasattr(s1, "auth_token"):
            s1.auth_token = r1["worker_token"]
        if hasattr(s2, "auth_token"):
            s2.auth_token = r2["worker_token"]
        return r1, r2
    def _wait_for(self, pred, timeout_s=5.0):
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            if pred():
                return True
            time.sleep(0.03)
        return bool(pred())
    def _assert_clean_resources(self):
        tel = SCHEDULER.telemetry()
        self.assertEqual(tel["active_requests"], 0)
        rs = CLUSTER.resilience_status()
        self.assertEqual(rs["inflight"], [])
    def test_01_worker_disconnect_before_generation(self):
        s1 = pipeline_worker.PipelineWorkerServer(host="127.0.0.1", port=0, max_threads=2, allow_cuda=False)
        s2 = pipeline_worker.PipelineWorkerServer(host="127.0.0.1", port=0, max_threads=2, allow_cuda=False)
        s1.start()
        s2.start()
        self._servers.extend([s1, s2])
        resilience.POLICY.configure(max_recovery_attempts=0, heartbeat_interval_s=0.3, monitor_tick_s=0.1)
        _, r2 = self._setup_two_worker_cluster(s1, s2, coord_contrib=False)
        CLUSTER.deploy_cluster(verify=False)
        s2.stop()
        CLUSTER.leave_worker("w2", r2["worker_token"])
        self.assertEqual(CLUSTER.state, "DEGRADED")
        resp = self.client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hello shard"}], "max_tokens": 4})
        self.assertEqual(resp.status_code, 503)
        err = resp.json()["error"]
        self.assertEqual(err["code"], "CLUSTER_UNAVAILABLE")
        self.assertFalse(err["cluster_available"])
        self._assert_clean_resources()
    def test_02_worker_crash_during_load_and_prefill(self):
        s_bad = _ScriptedPeerServer(behavior="crash_on_load")
        s_bad.start()
        self._servers.append(s_bad)
        resilience.POLICY.configure(load_timeout_s=2.0, poll_slice_s=0.05)
        CLUSTER.create_cluster(name="LoadFail", model_id=self.model_dir, strategy="torch_pipeline_parallel", ctx_tokens=128, parallel_slots=1, open_net=True, coordinator_contributes=True, auto_deploy=False, fetch_model_meta=False)
        CLUSTER.register_worker(worker_id="w_load_bad", name="BadLoader", hw=self._hw(8192), quota={"max_ram_gb": 4}, consented=True, pipeline_port=s_bad.port, pipeline_host="127.0.0.1")
        with self.assertRaises(RuntimeError):
            CLUSTER.deploy_cluster(verify=False)
        self.assertNotEqual(CLUSTER.state, "LOADING")
        CLUSTER.stop_cluster()
        resilience.POLICY.reset()
        resilience.POLICY.configure(max_recovery_attempts=1, recovery_wait_s=5.0, retry_backoff_s=0.05, heartbeat_interval_s=0.3, monitor_tick_s=0.08, poll_slice_s=0.05)
        s1 = pipeline_worker.PipelineWorkerServer(host="127.0.0.1", port=0, max_threads=2, allow_cuda=False)
        s1.start()
        s_prefill_bad = _ScriptedPeerServer(behavior="ok", fail_after_forwards=0)
        s_prefill_bad.start()
        self._servers.extend([s1, s_prefill_bad])
        r1, _ = self._setup_two_worker_cluster(s1, s_prefill_bad, coord_contrib=True)
        self._start_hb("w1", r1["worker_token"])
        plan = {
            "feasible": True,
            "strategy": "torch_pipeline_parallel",
            "stages": [
                {"stage_idx": 0, "node_id": "w1", "worker_id": "w1", "role": "worker", "endpoint": f"127.0.0.1:{self._srv_port(s1)}", "layer_start": 0, "layer_end": 2, "has_embed": True, "has_lm_head": False, "shard_files": ["model-00001-of-00002.safetensors"]},
                {"stage_idx": 1, "node_id": "w2", "worker_id": "w2", "role": "worker", "endpoint": f"127.0.0.1:{self._srv_port(s_prefill_bad)}", "layer_start": 2, "layer_end": 4, "has_embed": False, "has_lm_head": True, "shard_files": ["model-00002-of-00002.safetensors"]},
            ],
        }
        pc = pipeline_coordinator.PipelineCoordinator(self.model_dir, plan, worker_tokens={"w1": CLUSTER.workers["w1"]["worker_token"], "w2": CLUSTER.workers["w2"]["worker_token"]}, cluster_id=CLUSTER.cluster_id)
        pc.on_worker_failure = CLUSTER._on_data_plane_failure
        pc.start()
        with CLUSTER._lock:
            CLUSTER.workers["w1"]["assigned_stage"] = plan["stages"][0]
            CLUSTER.workers["w2"]["assigned_stage"] = plan["stages"][1]
            CLUSTER._pipeline_coord = pc
            CLUSTER.state = "READY"
            CLUSTER.ready_at = time.time()
        evs_02 = list(CLUSTER.generate_stream([{"role": "user", "content": "hello shard"}], max_tokens=3, temperature=0.0))
        tokens = [ev["token"] for ev in evs_02 if ev.get("type") == "content"]
        done_02 = [ev for ev in evs_02 if ev.get("type") == "done"]
        self.assertEqual(len(done_02), 1)
        self.assertEqual(done_02[0]["usage"]["completion_tokens"], 3)
        self.assertGreaterEqual(len(tokens), 2)
        self.assertGreaterEqual(resilience.METRICS.snapshot()["counters"].get("recovery_success_total", 0), 1)
        self.assertEqual(s1.stats()["active_kv_sessions"], 0)
        self._assert_clean_resources()
    def test_03_worker_disconnect_during_decode_and_case_a_resume_no_duplicates(self):
        resilience.POLICY.configure(max_recovery_attempts=1, recovery_wait_s=6.0, retry_backoff_s=0.05, heartbeat_interval_s=0.3, monitor_tick_s=0.08, poll_slice_s=0.05, enable_resume=True)
        s1 = pipeline_worker.PipelineWorkerServer(host="127.0.0.1", port=0, max_threads=2, allow_cuda=False)
        s2 = pipeline_worker.PipelineWorkerServer(host="127.0.0.1", port=0, max_threads=2, allow_cuda=False)
        s1.start()
        s2.start()
        self._servers.extend([s1, s2])
        r1, r2 = self._setup_two_worker_cluster(s1, s2, coord_contrib=True)
        self._start_hb("w1", r1["worker_token"])
        hb2 = self._start_hb("w2", r2["worker_token"])
        CLUSTER.deploy_cluster(verify=False)
        base_evs = list(CLUSTER.generate_stream([{"role": "user", "content": "hello shard"}], max_tokens=5, temperature=0.0))
        baseline_tokens = [ev["token"] for ev in base_evs if ev.get("type") == "content"]
        self.assertEqual([ev for ev in base_evs if ev.get("type") == "done"][0]["usage"]["completion_tokens"], 5)
        self.assertGreaterEqual(len(baseline_tokens), 4)
        stream = CLUSTER.generate_stream([{"role": "user", "content": "hello shard"}], max_tokens=5, temperature=0.0)
        first_two = []
        for ev in stream:
            if ev.get("type") == "content":
                first_two.append(ev["token"])
                if len(first_two) == 2:
                    hb2.set()
                    s2.stop()
                    break
        rest = []
        done_evs = []
        for ev in stream:
            if ev.get("type") == "content":
                rest.append(ev["token"])
            elif ev.get("type") == "done":
                done_evs.append(ev)
        combined = first_two + rest
        self.assertEqual(combined, baseline_tokens)
        self.assertEqual(len(done_evs), 1)
        self.assertTrue(done_evs[0]["telemetry"]["resumed_after_failure"])
        self.assertEqual(CLUSTER.workers["w1"]["state"], resilience.HEALTHY)
        self.assertEqual(CLUSTER.workers["w2"]["state"], resilience.FAILED)
        self.assertEqual(s1.stats()["active_kv_sessions"], 0)
        self._assert_clean_resources()
    def test_04_heartbeat_loss_state_transitions_and_abort(self):
        resilience.POLICY.configure(heartbeat_interval_s=0.15, suspect_missed=2, dead_missed=4, monitor_tick_s=0.05, poll_slice_s=0.04, recovery_wait_s=0.5, max_recovery_attempts=0)
        s_slow = _ScriptedPeerServer(behavior="blackhole")
        s_slow.start()
        self._servers.append(s_slow)
        CLUSTER.create_cluster(name="HBLoss", model_id=self.model_dir, strategy="torch_pipeline_parallel", ctx_tokens=128, parallel_slots=1, open_net=True, coordinator_contributes=False, auto_deploy=False, fetch_model_meta=False)
        CLUSTER.register_worker(worker_id="w_hb", name="HBWorker", hw=self._hw(2048), quota={"max_ram_gb": 2}, consented=True, pipeline_port=s_slow.port, pipeline_host="127.0.0.1")
        plan = {
            "feasible": True,
            "strategy": "torch_pipeline_parallel",
            "stages": [
                {"stage_idx": 0, "node_id": "w_hb", "worker_id": "w_hb", "role": "worker", "endpoint": f"127.0.0.1:{s_slow.port}", "layer_start": 0, "layer_end": 4, "has_embed": True, "has_lm_head": True},
            ],
        }
        pc = pipeline_coordinator.PipelineCoordinator(self.model_dir, plan, worker_tokens={"w_hb": CLUSTER.workers["w_hb"]["worker_token"]}, cluster_id=CLUSTER.cluster_id)
        pc.start()
        with CLUSTER._lock:
            CLUSTER.workers["w_hb"]["assigned_stage"] = plan["stages"][0]
            CLUSTER._pipeline_coord = pc
            CLUSTER.state = "READY"
            CLUSTER.ready_at = time.time()
        CLUSTER.workers["w_hb"]["last_heartbeat"] = time.time() - (resilience.POLICY.suspect_after_s + 0.05)
        CLUSTER._rebalance_needed.set()
        self.assertTrue(self._wait_for(lambda: CLUSTER.workers["w_hb"]["state"] == resilience.SUSPECT))
        self.assertTrue(self._wait_for(lambda: CLUSTER.workers["w_hb"]["state"] == resilience.FAILED, timeout_s=2.0))
        self.assertEqual(CLUSTER.state, "DEGRADED")
        self.assertEqual(CLUSTER._alive_worker_records(), [])
        snap = resilience.METRICS.snapshot()["counters"]
        self.assertGreaterEqual(snap.get("heartbeat_missed_total", 0), 1)
        self.assertGreaterEqual(snap.get("heartbeat_loss_total", 0), 1)
    def test_05_worker_connected_blackhole_stops_responding(self):
        resilience.POLICY.configure(heartbeat_interval_s=5.0, suspect_missed=2, dead_missed=5, prefill_timeout_s=0.35, decode_step_timeout_s=0.35, poll_slice_s=0.05, max_recovery_attempts=0, monitor_tick_s=0.1)
        s_bh = _ScriptedPeerServer(behavior="blackhole")
        s_bh.start()
        self._servers.append(s_bh)
        CLUSTER.create_cluster(name="Blackhole", model_id=self.model_dir, strategy="torch_pipeline_parallel", ctx_tokens=128, parallel_slots=1, open_net=True, coordinator_contributes=False, auto_deploy=False, fetch_model_meta=False)
        CLUSTER.register_worker(worker_id="w_bh", name="BlackholeW", hw=self._hw(2048), quota={"max_ram_gb": 2}, consented=True, pipeline_port=s_bh.port, pipeline_host="127.0.0.1")
        plan = {
            "feasible": True,
            "strategy": "torch_pipeline_parallel",
            "stages": [
                {"stage_idx": 0, "node_id": "w_bh", "worker_id": "w_bh", "role": "worker", "endpoint": f"127.0.0.1:{s_bh.port}", "layer_start": 0, "layer_end": 4, "has_embed": True, "has_lm_head": True},
            ],
        }
        pc = pipeline_coordinator.PipelineCoordinator(self.model_dir, plan, worker_tokens={"w_bh": CLUSTER.workers["w_bh"]["worker_token"]}, cluster_id=CLUSTER.cluster_id)
        pc.on_worker_failure = CLUSTER._on_data_plane_failure
        pc.start()
        with CLUSTER._lock:
            CLUSTER.workers["w_bh"]["assigned_stage"] = plan["stages"][0]
            CLUSTER._pipeline_coord = pc
            CLUSTER.state = "READY"
            CLUSTER.ready_at = time.time()
        t0 = time.monotonic()
        with self.assertRaises(resilience.InferenceFailure) as ctx_err:
            list(CLUSTER.generate_stream([{"role": "user", "content": "hello shard"}], max_tokens=2))
        dt = time.monotonic() - t0
        self.assertLess(dt, 1.5)
        self.assertEqual(ctx_err.exception.code, "WORKER_TIMEOUT")
        self.assertEqual(ctx_err.exception.worker_id, "w_bh")
        self.assertEqual(CLUSTER.workers["w_bh"]["state"], resilience.FAILED)
        self._assert_clean_resources()
    def test_06_slow_worker_distinct_from_dead_worker(self):
        resilience.POLICY.configure(heartbeat_interval_s=0.3, suspect_missed=2, dead_missed=5, prefill_timeout_s=3.0, decode_step_timeout_s=3.0, slow_step_warn_s=0.15, poll_slice_s=0.05)
        s_slow = _ScriptedPeerServer(behavior="ok", delay_s=0.25)
        s_slow.start()
        self._servers.append(s_slow)
        CLUSTER.create_cluster(name="SlowCluster", model_id=self.model_dir, strategy="torch_pipeline_parallel", ctx_tokens=128, parallel_slots=1, open_net=True, coordinator_contributes=False, auto_deploy=False, fetch_model_meta=False)
        r = CLUSTER.register_worker(worker_id="w_slow", name="SlowW", hw=self._hw(2048), quota={"max_ram_gb": 2}, consented=True, pipeline_port=s_slow.port, pipeline_host="127.0.0.1")
        plan = {
            "feasible": True,
            "strategy": "torch_pipeline_parallel",
            "stages": [
                {"stage_idx": 0, "node_id": "w_slow", "worker_id": "w_slow", "role": "worker", "endpoint": f"127.0.0.1:{s_slow.port}", "layer_start": 0, "layer_end": 4, "has_embed": True, "has_lm_head": True},
            ],
        }
        pc = pipeline_coordinator.PipelineCoordinator(self.model_dir, plan, worker_tokens={"w_slow": r["worker_token"]}, cluster_id=CLUSTER.cluster_id)
        pc.on_worker_failure = CLUSTER._on_data_plane_failure
        pc.on_slow_step = CLUSTER._on_slow_step
        pc.start()
        with CLUSTER._lock:
            CLUSTER.workers["w_slow"]["assigned_stage"] = plan["stages"][0]
            CLUSTER._pipeline_coord = pc
            CLUSTER.state = "READY"
            CLUSTER.ready_at = time.time()
        hb_stop = threading.Event()
        def _hb():
            while not hb_stop.wait(0.15):
                CLUSTER.heartbeat_worker("w_slow", r["worker_token"])
        threading.Thread(target=_hb, daemon=True).start()
        try:
            evs = list(CLUSTER.generate_stream([{"role": "user", "content": "hello shard"}], max_tokens=2, temperature=0.0))
        finally:
            hb_stop.set()
        self.assertTrue(any(e.get("type") == "done" for e in evs))
        self.assertEqual(CLUSTER.workers["w_slow"]["state"], resilience.HEALTHY)
        self.assertTrue(CLUSTER.workers["w_slow"].get("slow"))
        self.assertGreaterEqual(resilience.METRICS.snapshot()["counters"].get("slow_steps_total", 0), 1)
    def test_07_streaming_client_error_contract_openai_and_anthropic(self):
        resilience.POLICY.configure(max_recovery_attempts=0, poll_slice_s=0.05, enable_resume=False)
        s_drop = _ScriptedPeerServer(behavior="ok", fail_after_forwards=1)
        s_drop.start()
        self._servers.append(s_drop)
        CLUSTER.create_cluster(name="StreamFail", model_id=self.model_dir, strategy="torch_pipeline_parallel", ctx_tokens=128, parallel_slots=2, open_net=True, coordinator_contributes=False, auto_deploy=False, fetch_model_meta=False)
        r = CLUSTER.register_worker(worker_id="w_str", name="StreamW", hw=self._hw(2048), quota={"max_ram_gb": 2}, consented=True, pipeline_port=s_drop.port, pipeline_host="127.0.0.1")
        plan = {
            "feasible": True,
            "strategy": "torch_pipeline_parallel",
            "stages": [{"stage_idx": 0, "node_id": "w_str", "worker_id": "w_str", "role": "worker", "endpoint": f"127.0.0.1:{s_drop.port}", "layer_start": 0, "layer_end": 4, "has_embed": True, "has_lm_head": True}],
        }
        pc = pipeline_coordinator.PipelineCoordinator(self.model_dir, plan, worker_tokens={"w_str": r["worker_token"]}, cluster_id=CLUSTER.cluster_id)
        pc.on_worker_failure = CLUSTER._on_data_plane_failure
        pc.start()
        with CLUSTER._lock:
            CLUSTER.workers["w_str"]["assigned_stage"] = plan["stages"][0]
            CLUSTER._pipeline_coord = pc
            CLUSTER.state = "READY"
            CLUSTER.ready_at = time.time()
        with self.client.stream("POST", "/v1/chat/completions", json={"messages": [{"role": "user", "content": "hello shard"}], "max_tokens": 5, "stream": True}) as resp:
            body_oa = "".join(resp.iter_text())
        self.assertIn('"code": "WORKER_LOST"', body_oa)
        self.assertIn('"worker_id": "w_str"', body_oa)
        self.assertIn("data: [DONE]", body_oa)
        self.assertNotIn('"finish_reason": "stop"', body_oa)
        s_drop.stop()
        CLUSTER.stop_cluster()
        s_drop2 = _ScriptedPeerServer(behavior="ok", fail_after_forwards=1)
        s_drop2.start()
        self._servers.append(s_drop2)
        CLUSTER.create_cluster(name="StreamFailAnth", model_id=self.model_dir, strategy="torch_pipeline_parallel", ctx_tokens=128, parallel_slots=2, open_net=True, coordinator_contributes=False, auto_deploy=False, fetch_model_meta=False)
        r2 = CLUSTER.register_worker(worker_id="w_str2", name="StreamW2", hw=self._hw(2048), quota={"max_ram_gb": 2}, consented=True, pipeline_port=s_drop2.port, pipeline_host="127.0.0.1")
        plan2 = {"feasible": True, "strategy": "torch_pipeline_parallel", "stages": [{"stage_idx": 0, "node_id": "w_str2", "worker_id": "w_str2", "role": "worker", "endpoint": f"127.0.0.1:{s_drop2.port}", "layer_start": 0, "layer_end": 4, "has_embed": True, "has_lm_head": True}]}
        pc2 = pipeline_coordinator.PipelineCoordinator(self.model_dir, plan2, worker_tokens={"w_str2": r2["worker_token"]}, cluster_id=CLUSTER.cluster_id)
        pc2.on_worker_failure = CLUSTER._on_data_plane_failure
        pc2.start()
        with CLUSTER._lock:
            CLUSTER.workers["w_str2"]["assigned_stage"] = plan2["stages"][0]
            CLUSTER._pipeline_coord = pc2
            CLUSTER.state = "READY"
            CLUSTER.ready_at = time.time()
        with self.client.stream("POST", "/v1/messages", json={"messages": [{"role": "user", "content": "hello shard"}], "max_tokens": 5, "stream": True}) as resp:
            body_an = "".join(resp.iter_text())
        self.assertIn("event: error", body_an)
        self.assertIn('"code": "WORKER_LOST"', body_an)
        self.assertNotIn('"stop_reason": "end_turn"', body_an)
        self._assert_clean_resources()
    def test_08_multi_worker_pipeline_failure_keeps_healthy_worker_alive(self):
        resilience.POLICY.configure(max_recovery_attempts=1, recovery_wait_s=6.0, retry_backoff_s=0.05, heartbeat_interval_s=0.3, monitor_tick_s=0.08, poll_slice_s=0.05)
        s1 = pipeline_worker.PipelineWorkerServer(host="127.0.0.1", port=0, max_threads=2, allow_cuda=False)
        s2 = _ScriptedPeerServer(behavior="ok", fail_after_forwards=1)
        s1.start()
        s2.start()
        self._servers.extend([s1, s2])
        r1, _ = self._setup_two_worker_cluster(s1, s2, coord_contrib=True, ram1=8192, ram2=8192)
        self._start_hb("w1", r1["worker_token"])
        plan = {
