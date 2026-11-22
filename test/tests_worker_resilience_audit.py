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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from fastapi.testclient import TestClient
from shard_engine import gguf_backend, server, training
from shard_engine.distributed import CLUSTER, SCHEDULER, pipeline_coordinator, pipeline_proto as proto, pipeline_worker, resilience
from test.tests_worker_resilience import _ScriptedPeerServer, _build_sharded_qwen2
def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p
def _parse_openai_sse(raw_text):
    tokens = []
    errors = []
    finish_reasons = []
    saw_done = False
    for block in raw_text.split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        data_parts = [ln[5:].strip() for ln in lines if ln.startswith("data:")]
        if not data_parts:
            continue
        payload = "\n".join(data_parts)
        if payload == "[DONE]":
            saw_done = True
            continue
        obj = json.loads(payload)
        if "error" in obj:
            errors.append(obj["error"])
            continue
        for ch in obj.get("choices") or []:
            tok = (ch.get("delta") or {}).get("content")
            if tok:
                tokens.append(tok)
            if ch.get("finish_reason"):
                finish_reasons.append(ch["finish_reason"])
    return {"tokens": tokens, "errors": errors, "finish_reasons": finish_reasons, "saw_done": saw_done}
def _parse_anthropic_sse(raw_text):
    events = []
    tokens = []
    errors = []
    stop_reasons = []
    for block in raw_text.split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        ev_type = None
        data_parts = []
        for ln in lines:
            if ln.startswith("event:"):
                ev_type = ln[6:].strip()
            elif ln.startswith("data:"):
                data_parts.append(ln[5:].strip())
        if not data_parts:
            continue
        obj = json.loads("\n".join(data_parts))
        events.append((ev_type or obj.get("type"), obj))
        if obj.get("type") == "content_block_delta":
            txt = (obj.get("delta") or {}).get("text")
            if txt:
                tokens.append(txt)
        elif obj.get("type") == "message_delta":
            sr = (obj.get("delta") or {}).get("stop_reason")
            if sr:
                stop_reasons.append(sr)
        elif obj.get("type") == "error":
            errors.append(obj.get("error") or {})
    return {"events": events, "tokens": tokens, "errors": errors, "stop_reasons": stop_reasons}
class _AuditPeerServer:
    def __init__(self, mode="blackhole_after", good_forwards=2, trickle_interval_s=0.03, vocab_size=8):
        self.mode = mode
        self.good_forwards = int(good_forwards)
        self.trickle_interval_s = float(trickle_interval_s)
        self.vocab_size = int(vocab_size)
        self.forward_count = 0
        self.release_count = 0
        self.stop_ev = threading.Event()
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
            if self.mode == "hang_auth":
                self.stop_ev.wait(10.0)
                return
            mtype, _, _ = proto.recv_frame(conn)
            if mtype != proto.MSG_AUTH:
                return
            proto.send_json_frame(conn, proto.MSG_AUTH_OK, {"status": "ok"})
            while not self.stop_ev.is_set():
                try:
                    mtype, _, payload = proto.recv_frame(conn)
                except socket.timeout:
                    continue
                if mtype == proto.MSG_LOAD_STAGE:
                    proto.send_json_frame(conn, proto.MSG_STAGE_READY, {"status": "ready"})
                elif mtype == proto.MSG_FORWARD:
                    meta, _ = proto.decode_tensor_payload(payload)
                    self.forward_count += 1
                    logits = torch.zeros((1, 1, self.vocab_size), dtype=torch.float32)
                    logits[0, 0, 2 + (self.forward_count % 5)] = 10.0
                    if self.mode == "missing_seq":
                        out_meta = {"request_id": meta.get("request_id"), "execution_id": meta.get("execution_id"), "stage_compute_ms": 1.0}
                        proto.send_frame(conn, proto.MSG_FORWARD_RES, proto.encode_tensor_payload(out_meta, logits))
                        continue
                    if self.mode == "missing_exec":
                        out_meta = {"request_id": meta.get("request_id"), "seq": meta.get("seq"), "stage_compute_ms": 1.0}
                        proto.send_frame(conn, proto.MSG_FORWARD_RES, proto.encode_tensor_payload(out_meta, logits))
                        continue
                    if self.mode == "missing_req":
                        out_meta = {"seq": meta.get("seq"), "execution_id": meta.get("execution_id"), "stage_compute_ms": 1.0}
                        proto.send_frame(conn, proto.MSG_FORWARD_RES, proto.encode_tensor_payload(out_meta, logits))
                        continue
                    if self.forward_count > self.good_forwards:
                        if self.mode == "blackhole_after":
                            self.stop_ev.wait(10.0)
                            return
                        if self.mode == "trickle_after":
                            out_meta = {"request_id": meta.get("request_id"), "seq": meta.get("seq"), "execution_id": meta.get("execution_id"), "stage_compute_ms": 1.0}
                            body = proto.encode_tensor_payload(out_meta, logits)
                            hdr = proto._HDR_STRUCT.pack(proto.MAGIC, proto.PROTO_VER, proto.MSG_FORWARD_RES, 0, len(body))
                            raw = hdr + body
                            for i in range(len(raw)):
                                if self.stop_ev.is_set():
                                    return
                                conn.sendall(raw[i:i + 1])
                                self.stop_ev.wait(self.trickle_interval_s)
                            continue
                        if self.mode == "drop_after":
                            return
                    out_meta = {"request_id": meta.get("request_id"), "seq": meta.get("seq"), "execution_id": meta.get("execution_id"), "stage_compute_ms": 1.0}
                    proto.send_frame(conn, proto.MSG_FORWARD_RES, proto.encode_tensor_payload(out_meta, logits))
                elif mtype == proto.MSG_RELEASE_KV:
                    if self.mode == "hang_release":
                        self.stop_ev.wait(5.0)
                        return
                    self.release_count += 1
                    proto.send_json_frame(conn, proto.MSG_PONG, {"released": True})
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
class TestWorkerResilienceAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["SHARD_ALLOW_INSECURE_LOCAL"] = "1"
        cls.tmp_dir = tempfile.mkdtemp(prefix="shard_audit_")
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
        SCHEDULER.configure(max_concurrent=4, max_queue_size=64, default_rpm=200, default_timeout_s=300.0)
        self.client = TestClient(server.app, client=("127.0.0.1", 50000))
        self.owner_hdr = {"X-Shard-Owner-Token": training.get_owner_token()}
        self._servers = []
        self._procs = []
        self._hb_stops = []
    def tearDown(self):
        for ev in self._hb_stops:
            ev.set()
        for p in self._procs:
            try:
                if p.poll() is None:
                    p.kill()
                p.wait(timeout=3.0)
            except Exception:
                pass
            for strm in (p.stdout, p.stderr):
                if strm is not None:
                    try:
                        strm.close()
                    except Exception:
                        pass
        for s in self._servers:
            try:
                s.stop()
            except Exception:
                pass
        CLUSTER.stop_cluster()
        CLUSTER.stop_local_worker()
        resilience.POLICY.reset()
    def _hw(self, ram_mb=8192):
        return {"vram_total_mb": 0, "vram_free_mb": 0, "ram_total_mb": ram_mb, "ram_avail_mb": ram_mb, "cpu_cores": 4, "backends": {"torch_pipeline": True}}
    def _start_hb(self, worker_id, token, interval=0.06):
        ev = threading.Event()
        self._hb_stops.append(ev)
        def _loop():
            seq = 0
            while not ev.wait(interval):
                seq += 1
                CLUSTER.heartbeat_worker(worker_id, token, hb_seq=seq, sent_at=time.time())
        threading.Thread(target=_loop, daemon=True).start()
        return ev
    def _spawn_worker_procs(self, specs):
        script = (
            "import sys, time\n"
            "from shard_engine.distributed.pipeline_worker import PipelineWorkerServer\n"
            "srv = PipelineWorkerServer(host='127.0.0.1', port=int(sys.argv[1]), auth_token=sys.argv[2], max_threads=2, allow_cuda=False)\n"
            "srv.start()\n"
            "print('READY', flush=True)\n"
            "while True:\n"
            "    time.sleep(0.5)\n"
        )
        procs = []
        for port, auth_token in specs:
            p = subprocess.Popen([sys.executable, "-c", script, str(port), auth_token], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self._procs.append(p)
            procs.append(p)
        for p in procs:
            line = p.stdout.readline().strip()
            self.assertEqual(line, "READY")
        return procs
    def _mount_single_peer(self, wid, srv, coord_contrib=False):
        CLUSTER.create_cluster(name=f"Audit-{wid}", model_id=self.model_dir, strategy="torch_pipeline_parallel", ctx_tokens=128, parallel_slots=4, open_net=True, coordinator_contributes=coord_contrib, auto_deploy=False, fetch_model_meta=False)
        r = CLUSTER.register_worker(worker_id=wid, name=wid, hw=self._hw(4096), quota={"max_ram_gb": 2}, consented=True, pipeline_port=srv.port, pipeline_host="127.0.0.1")
        plan = {"feasible": True, "strategy": "torch_pipeline_parallel", "stages": [{"stage_index": 0, "node_id": wid, "worker_id": wid, "role": "worker", "endpoint": f"127.0.0.1:{srv.port}", "layer_start": 0, "layer_end": 4, "has_embed": True, "has_lm_head": True}]}
        pc = pipeline_coordinator.PipelineCoordinator(self.model_dir, plan, worker_tokens={wid: r["worker_token"]}, cluster_id=CLUSTER.cluster_id)
        pc.on_worker_failure = CLUSTER._on_data_plane_failure
        pc.start()
        with CLUSTER._lock:
            CLUSTER.workers[wid]["assigned_stage"] = plan["stages"][0]
            CLUSTER._deployed_workers = {wid}
            CLUSTER._pipeline_coord = pc
            CLUSTER.state = "READY"
            CLUSTER.ready_at = time.time()
        return r, pc
    def _assert_clean_resources(self):
        end = time.monotonic() + 1.5
        while time.monotonic() < end:
            if SCHEDULER.telemetry()["active_requests"] == 0 and CLUSTER.resilience_status()["inflight"] == []:
                break
            time.sleep(0.03)
        self.assertEqual(SCHEDULER.telemetry()["active_requests"], 0)
        self.assertEqual(CLUSTER.resilience_status()["inflight"], [])
    def test_01_resume_state_context_replay_sampling_multi_retry_and_isolation(self):
        resilience.POLICY.configure(max_recovery_attempts=2, recovery_wait_s=6.0, retry_backoff_s=0.05, heartbeat_interval_s=0.3, monitor_tick_s=0.06, poll_slice_s=0.04, enable_resume=True)
        s1 = pipeline_worker.PipelineWorkerServer(host="127.0.0.1", port=0, max_threads=2, allow_cuda=False)
        s2 = pipeline_worker.PipelineWorkerServer(host="127.0.0.1", port=0, max_threads=2, allow_cuda=False)
        p1, p2 = s1.start(), s2.start()
        self._servers.extend([s1, s2])
        CLUSTER.create_cluster(name="SamplingResume", model_id=self.model_dir, strategy="torch_pipeline_parallel", ctx_tokens=128, parallel_slots=4, open_net=True, coordinator_contributes=True, auto_deploy=False, fetch_model_meta=False)
        r1 = CLUSTER.register_worker(worker_id="w1", name="W1", hw=self._hw(8192), quota={"max_ram_gb": 4}, consented=True, pipeline_port=p1, pipeline_host="127.0.0.1")
        r2 = CLUSTER.register_worker(worker_id="w2", name="W2", hw=self._hw(8192), quota={"max_ram_gb": 4}, consented=True, pipeline_port=p2, pipeline_host="127.0.0.1")
        s1.auth_token, s2.auth_token = r1["worker_token"], r2["worker_token"]
        self._start_hb("w1", r1["worker_token"])
        hb2 = self._start_hb("w2", r2["worker_token"])
        CLUSTER.deploy_cluster(verify=False)
        base_rep = list(CLUSTER.generate_stream([{"role": "user", "content": "hello shard"}], max_tokens=6, temperature=0.0, repetition_penalty=1.45))
        base_rep_tokens = [e["token"] for e in base_rep if e.get("type") == "content"]
        self.assertGreaterEqual(len(base_rep_tokens), 4)
        base_b = list(CLUSTER.generate_stream([{"role": "user", "content": "alpha beta worker"}], max_tokens=5, temperature=0.0, top_k=1))
        base_b_tokens = [e["token"] for e in base_b if e.get("type") == "content"]
        out_a, out_b = [], []
        done_a, done_b = [], []
        started_a = threading.Event()
        def _req_a():
            st = CLUSTER.generate_stream([{"role": "user", "content": "hello shard"}], max_tokens=6, temperature=0.0, repetition_penalty=1.45)
            for ev in st:
                if ev.get("type") == "content":
                    out_a.append(ev["token"])
                    if len(out_a) == 2:
                        started_a.set()
                        time.sleep(0.08)
                        hb2.set()
                        s2.stop()
                elif ev.get("type") == "done":
                    done_a.append(ev)
        def _req_b():
            started_a.wait(timeout=3.0)
            st = CLUSTER.generate_stream([{"role": "user", "content": "alpha beta worker"}], max_tokens=5, temperature=0.0, top_k=1)
            for ev in st:
                if ev.get("type") == "content":
                    out_b.append(ev["token"])
                elif ev.get("type") == "done":
                    done_b.append(ev)
        t_a = threading.Thread(target=_req_a)
        t_b = threading.Thread(target=_req_b)
        t_a.start()
        t_b.start()
        t_a.join(timeout=10.0)
        t_b.join(timeout=10.0)
        self.assertEqual(out_a, base_rep_tokens)
        self.assertEqual(out_b, base_b_tokens)
        self.assertEqual(len(done_a), 1)
        self.assertTrue(done_a[0]["telemetry"]["resumed_after_failure"])
        self.assertEqual(len(done_b), 1)
        self.assertEqual(s1.stats()["active_kv_sessions"], 0)
        pc_cur = CLUSTER._pipeline_coord
        old_run = pc_cur.is_running
        pc_cur.is_running = lambda: False
        try:
            with self.assertRaises(resilience.InferenceFailure) as ce:
                list(pc_cur.generate_stream([{"role": "user", "content": "hello"}], max_tokens=4, resume=resilience.ResumeState([2, 3], "hello shard")))
            self.assertIsNotNone(ce.exception.resume)
            self.assertEqual(ce.exception.resume.generated_ids, [2, 3])
            self.assertEqual(ce.exception.resume.delivered_text, "hello shard")
        finally:
            pc_cur.is_running = old_run
        self._assert_clean_resources()
    def test_02_real_multiprocess_abrupt_kill_mid_generation_recovery(self):
        p1, p2 = _free_port(), _free_port()
        CLUSTER.create_cluster(name="MultiProcKill", model_id=self.model_dir, strategy="torch_pipeline_parallel", ctx_tokens=128, parallel_slots=2, open_net=True, coordinator_contributes=True, auto_deploy=False, fetch_model_meta=False)
        r1 = CLUSTER.register_worker(worker_id="proc-w1", name="ProcW1", hw=self._hw(8192), quota={"max_ram_gb": 4}, consented=True, pipeline_port=p1, pipeline_host="127.0.0.1")
        r2 = CLUSTER.register_worker(worker_id="proc-w2", name="ProcW2", hw=self._hw(8192), quota={"max_ram_gb": 4}, consented=True, pipeline_port=p2, pipeline_host="127.0.0.1")
        self._start_hb("proc-w1", r1["worker_token"])
        hb2 = self._start_hb("proc-w2", r2["worker_token"])
        proc1, proc2 = self._spawn_worker_procs([(p1, r1["worker_token"]), (p2, r2["worker_token"])])
        resilience.POLICY.configure(max_recovery_attempts=1, recovery_wait_s=8.0, retry_backoff_s=0.05, heartbeat_interval_s=0.3, monitor_tick_s=0.06, poll_slice_s=0.04, enable_resume=True)
        CLUSTER.deploy_cluster(verify=False)
        base_r = self.client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hello shard"}], "max_tokens": 6, "temperature": 0.0})
        self.assertEqual(base_r.status_code, 200)
        baseline_text = base_r.json()["choices"][0]["message"]["content"]
        self.assertTrue(len(baseline_text) > 0)
        stream = CLUSTER.generate_stream([{"role": "user", "content": "hello shard"}], max_tokens=6, temperature=0.0)
        pre_kill = []
        for ev in stream:
            if ev.get("type") == "content":
                pre_kill.append(ev["token"])
                if len(pre_kill) == 2:
                    hb2.set()
                    proc2.kill()
                    proc2.wait(timeout=3.0)
                    break
        post_kill = []
        done_evs = []
        for ev in stream:
            if ev.get("type") == "content":
                post_kill.append(ev["token"])
            elif ev.get("type") == "done":
                done_evs.append(ev)
        self.assertEqual("".join(pre_kill + post_kill), baseline_text)
        self.assertEqual(len(done_evs), 1)
        self.assertTrue(done_evs[0]["telemetry"]["resumed_after_failure"])
        self.assertEqual(CLUSTER.workers["proc-w1"]["state"], resilience.HEALTHY)
        self.assertEqual(CLUSTER.workers["proc-w1"]["fail_count"], 0)
        self.assertEqual(CLUSTER.workers["proc-w2"]["state"], resilience.FAILED)
        self.assertIsNone(proc1.poll())
        after_r = self.client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hello shard"}], "max_tokens": 3, "temperature": 0.0})
        self.assertEqual(after_r.status_code, 200)
        self._assert_clean_resources()
    def test_03_real_multiprocess_abrupt_kill_unrecoverable_error_fields(self):
        p1, p2 = _free_port(), _free_port()
        CLUSTER.create_cluster(name="MultiProcTerm", model_id=self.model_dir, strategy="torch_pipeline_parallel", ctx_tokens=128, parallel_slots=2, open_net=True, coordinator_contributes=False, auto_deploy=False, fetch_model_meta=False)
        r1 = CLUSTER.register_worker(worker_id="pw-a", name="PWA", hw=self._hw(8192), quota={"max_ram_gb": 4}, consented=True, pipeline_port=p1, pipeline_host="127.0.0.1")
        r2 = CLUSTER.register_worker(worker_id="pw-b", name="PWB", hw=self._hw(8192), quota={"max_ram_gb": 4}, consented=True, pipeline_port=p2, pipeline_host="127.0.0.1")
        self._start_hb("pw-a", r1["worker_token"])
        hb2 = self._start_hb("pw-b", r2["worker_token"])
        _, proc2 = self._spawn_worker_procs([(p1, r1["worker_token"]), (p2, r2["worker_token"])])
        resilience.POLICY.configure(max_recovery_attempts=0, heartbeat_interval_s=0.3, monitor_tick_s=0.06, poll_slice_s=0.04, enable_resume=False)
        CLUSTER.deploy_cluster(verify=False)
        stream = CLUSTER.generate_stream([{"role": "user", "content": "hello shard"}], max_tokens=6, temperature=0.0, request_id="sreq-audit-kill")
        delivered = []
        for ev in stream:
            if ev.get("type") == "content":
                delivered.append(ev["token"])
                if len(delivered) == 2:
                    hb2.set()
                    proc2.kill()
                    proc2.wait(timeout=3.0)
                    break
        with self.assertRaises(resilience.InferenceFailure) as fe:
            for ev in stream:
                if ev.get("type") == "content":
                    delivered.append(ev["token"])
        err = fe.exception.to_error()
        self.assertEqual(err["code"], "WORKER_LOST")
        self.assertEqual(err["request_id"], "sreq-audit-kill")
        self.assertEqual(err["worker_id"], "pw-b")
        self.assertEqual(err["stage"], 1)
        self.assertEqual(err["tokens_delivered"], 2)
        self.assertEqual(CLUSTER.workers["pw-a"]["state"], resilience.HEALTHY)
        self.assertEqual(CLUSTER.workers["pw-b"]["state"], resilience.FAILED)
        self._assert_clean_resources()
    def test_04_graceful_shutdown_blackhole_and_slow_trickle_mid_generation(self):
        resilience.POLICY.configure(max_recovery_attempts=0, prefill_timeout_s=1.0, decode_step_timeout_s=0.35, poll_slice_s=0.04, enable_resume=False)
        s_bh = _AuditPeerServer(mode="blackhole_after", good_forwards=2)
        s_bh.start()
        self._servers.append(s_bh)
        self._mount_single_peer("w_bh_mid", s_bh, coord_contrib=False)
        t0 = time.monotonic()
        with self.client.stream("POST", "/v1/chat/completions", json={"messages": [{"role": "user", "content": "hello"}], "max_tokens": 6, "stream": True}) as resp:
            parsed = _parse_openai_sse("".join(resp.iter_text()))
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertEqual(len(parsed["tokens"]), 2)
        self.assertEqual(len(parsed["errors"]), 1)
        self.assertEqual(parsed["errors"][0]["code"], "WORKER_TIMEOUT")
        self.assertEqual(parsed["errors"][0]["worker_id"], "w_bh_mid")
        self.assertEqual(parsed["errors"][0]["stage"], 0)
        self.assertEqual(parsed["errors"][0]["tokens_delivered"], 2)
        self.assertEqual(parsed["finish_reasons"], [])
        self.assertEqual(CLUSTER.workers["w_bh_mid"]["state"], resilience.FAILED)
        CLUSTER.stop_cluster()
        s_trickle = _AuditPeerServer(mode="trickle_after", good_forwards=1, trickle_interval_s=0.03)
        s_trickle.start()
        self._servers.append(s_trickle)
        self._mount_single_peer("w_trickle", s_trickle, coord_contrib=False)
        t1 = time.monotonic()
        with self.client.stream("POST", "/v1/messages", json={"messages": [{"role": "user", "content": "hello"}], "max_tokens": 6, "stream": True}) as resp:
            parsed_an = _parse_anthropic_sse("".join(resp.iter_text()))
        self.assertLess(time.monotonic() - t1, 2.0)
        self.assertEqual(len(parsed_an["tokens"]), 1)
        self.assertEqual(len(parsed_an["errors"]), 1)
        self.assertEqual(parsed_an["errors"][0]["code"], "WORKER_TIMEOUT")
        self.assertEqual(parsed_an["stop_reasons"], [])
        CLUSTER.stop_cluster()
        resilience.POLICY.configure(max_recovery_attempts=1, recovery_wait_s=6.0, retry_backoff_s=0.05, heartbeat_interval_s=0.3, monitor_tick_s=0.06, poll_slice_s=0.04, enable_resume=True)
        s1 = pipeline_worker.PipelineWorkerServer(host="127.0.0.1", port=0, max_threads=2, allow_cuda=False)
        s2 = pipeline_worker.PipelineWorkerServer(host="127.0.0.1", port=0, max_threads=2, allow_cuda=False)
        p1, p2 = s1.start(), s2.start()
        self._servers.extend([s1, s2])
        CLUSTER.create_cluster(name="GracefulLeave", model_id=self.model_dir, strategy="torch_pipeline_parallel", ctx_tokens=128, parallel_slots=2, open_net=True, coordinator_contributes=True, auto_deploy=False, fetch_model_meta=False)
        r1 = CLUSTER.register_worker(worker_id="gw1", name="GW1", hw=self._hw(8192), quota={"max_ram_gb": 4}, consented=True, pipeline_port=p1, pipeline_host="127.0.0.1")
        r2 = CLUSTER.register_worker(worker_id="gw2", name="GW2", hw=self._hw(8192), quota={"max_ram_gb": 4}, consented=True, pipeline_port=p2, pipeline_host="127.0.0.1")
        s1.auth_token, s2.auth_token = r1["worker_token"], r2["worker_token"]
        self._start_hb("gw1", r1["worker_token"])
        hb2 = self._start_hb("gw2", r2["worker_token"])
        CLUSTER.deploy_cluster(verify=False)
        base = [e["token"] for e in CLUSTER.generate_stream([{"role": "user", "content": "hello shard"}], max_tokens=5, temperature=0.0) if e.get("type") == "content"]
        st = CLUSTER.generate_stream([{"role": "user", "content": "hello shard"}], max_tokens=5, temperature=0.0)
        got = []
        for ev in st:
            if ev.get("type") == "content":
                got.append(ev["token"])
                if len(got) == 2:
                    hb2.set()
                    CLUSTER.leave_worker("gw2", r2["worker_token"])
                    s2.stop()
                    break
        for ev in st:
            if ev.get("type") == "content":
                got.append(ev["token"])
        self.assertEqual(got, base)
        self.assertEqual(CLUSTER.workers["gw1"]["state"], resilience.HEALTHY)
        self._assert_clean_resources()
    def test_05_sse_protocol_matrix_openai_and_anthropic(self):
        CLUSTER.create_cluster(name="PreHeader", model_id=self.model_dir, strategy="torch_pipeline_parallel", ctx_tokens=128, parallel_slots=2, open_net=True, coordinator_contributes=False, auto_deploy=False, fetch_model_meta=False)
        with CLUSTER._lock:
            CLUSTER.state = "DEGRADED"
        r_oa_pre = self.client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}], "stream": True})
        self.assertEqual(r_oa_pre.status_code, 503)
        self.assertEqual(r_oa_pre.headers.get("retry-after"), "5")
        self.assertEqual(r_oa_pre.json()["error"]["code"], "CLUSTER_UNAVAILABLE")
        r_an_pre = self.client.post("/v1/messages", json={"messages": [{"role": "user", "content": "hi"}], "stream": True})
        self.assertEqual(r_an_pre.status_code, 503)
        self.assertEqual(r_an_pre.headers.get("retry-after"), "5")
        self.assertEqual(r_an_pre.json()["error"]["code"], "CLUSTER_UNAVAILABLE")
        CLUSTER.stop_cluster()
        for good_n, expect_toks in ((0, 0), (1, 1), (3, 3)):
            resilience.POLICY.configure(max_recovery_attempts=0, poll_slice_s=0.04, enable_resume=False)
            srv_oa = _AuditPeerServer(mode="drop_after", good_forwards=good_n)
            srv_oa.start()
            self._servers.append(srv_oa)
            self._mount_single_peer(f"woa{good_n}", srv_oa, coord_contrib=False)
            with self.client.stream("POST", "/v1/chat/completions", json={"messages": [{"role": "user", "content": "hello"}], "max_tokens": 6, "stream": True}) as resp:
                self.assertEqual(resp.status_code, 200)
                oa = _parse_openai_sse("".join(resp.iter_text()))
            self.assertEqual(len(oa["tokens"]), expect_toks)
            self.assertEqual(len(oa["errors"]), 1)
            self.assertEqual(oa["errors"][0]["code"], "WORKER_LOST")
            self.assertEqual(oa["errors"][0]["tokens_delivered"], expect_toks)
            self.assertEqual(oa["finish_reasons"], [])
            self.assertTrue(oa["saw_done"])
            CLUSTER.stop_cluster()
            srv_an = _AuditPeerServer(mode="drop_after", good_forwards=good_n)
            srv_an.start()
            self._servers.append(srv_an)
            self._mount_single_peer(f"wan{good_n}", srv_an, coord_contrib=False)
            with self.client.stream("POST", "/v1/messages", json={"messages": [{"role": "user", "content": "hello"}], "max_tokens": 6, "stream": True}) as resp:
                self.assertEqual(resp.status_code, 200)
                an = _parse_anthropic_sse("".join(resp.iter_text()))
            self.assertEqual(len(an["tokens"]), expect_toks)
            self.assertEqual(len(an["errors"]), 1)
            self.assertEqual(an["errors"][0]["code"], "WORKER_LOST")
            self.assertEqual(an["errors"][0]["tokens_delivered"], expect_toks)
            self.assertEqual(an["stop_reasons"], [])
            ev_names = [e[0] for e in an["events"]]
            self.assertNotIn("message_stop", ev_names)
            if expect_toks == 0:
                self.assertNotIn("content_block_start", ev_names)
            CLUSTER.stop_cluster()
        resilience.POLICY.configure(max_recovery_attempts=1, recovery_wait_s=0.35, monitor_tick_s=0.05, poll_slice_s=0.04, enable_resume=True)
        srv_rec = _AuditPeerServer(mode="drop_after", good_forwards=2)
        srv_rec.start()
        self._servers.append(srv_rec)
        self._mount_single_peer("wrecfail", srv_rec, coord_contrib=False)
        with self.client.stream("POST", "/v1/messages", json={"messages": [{"role": "user", "content": "hello"}], "max_tokens": 6, "stream": True}) as resp:
            an_rec = _parse_anthropic_sse("".join(resp.iter_text()))
        self.assertEqual(len(an_rec["tokens"]), 2)
        self.assertEqual(len(an_rec["errors"]), 1)
        self.assertEqual(an_rec["errors"][0]["code"], "RECOVERY_FAILED")
        self.assertTrue(an_rec["errors"][0]["recovery_attempted"])
        self.assertEqual(an_rec["stop_reasons"], [])
        CLUSTER.stop_cluster()
        resilience.POLICY.configure(max_recovery_attempts=0, poll_slice_s=0.04)
        srv_can = _ScriptedPeerServer(behavior="ok", delay_s=0.18)
        srv_can.start()
        self._servers.append(srv_can)
        self._mount_single_peer("wcan", srv_can, coord_contrib=False)
        threading.Timer(0.1, SCHEDULER.cancel_all).start()
        with self.client.stream("POST", "/v1/messages", json={"messages": [{"role": "user", "content": "hello"}], "max_tokens": 6, "stream": True}) as resp:
            an_can = _parse_anthropic_sse("".join(resp.iter_text()))
        self.assertEqual(an_can["stop_reasons"], [])
        self.assertNotIn("message_stop", [e[0] for e in an_can["events"]])
        threading.Timer(0.1, SCHEDULER.cancel_all).start()
        with self.client.stream("POST", "/v1/chat/completions", json={"messages": [{"role": "user", "content": "hello"}], "max_tokens": 6, "stream": True}) as resp:
            oa_can = _parse_openai_sse("".join(resp.iter_text()))
        self.assertEqual(oa_can["finish_reasons"], [])
        threading.Timer(0.1, SCHEDULER.cancel_all).start()
        r_sync_can = self.client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hello"}], "max_tokens": 6})
        self.assertEqual(r_sync_can.status_code, 499)
        self.assertEqual(r_sync_can.json()["error"]["code"], "REQUEST_CANCELLED")
        self._assert_clean_resources()
    def test_06_timeout_interactions_keepalive_stall_load_abort_and_no_busy_loop(self):
        resilience.POLICY.configure(connect_timeout_s=0.25, load_timeout_s=5.0, poll_slice_s=0.04, max_recovery_attempts=0)
        s_auth = _AuditPeerServer(mode="hang_auth")
        s_auth.start()
        self._servers.append(s_auth)
        plan_auth = {"feasible": True, "strategy": "torch_pipeline_parallel", "stages": [{"stage_index": 0, "node_id": "wa", "worker_id": "wa", "role": "worker", "endpoint": f"127.0.0.1:{s_auth.port}", "layer_start": 0, "layer_end": 4, "has_embed": True, "has_lm_head": True}]}
        pc_auth = pipeline_coordinator.PipelineCoordinator(self.model_dir, plan_auth, worker_tokens={"wa": "t"}, cluster_id="c")
        t0 = time.monotonic()
        with self.assertRaises(RuntimeError):
            pc_auth.start()
        self.assertLess(time.monotonic() - t0, 1.5)
        pc_auth.stop()
        s_hl = _ScriptedPeerServer(behavior="hang_on_load")
        s_hl.start()
        self._servers.append(s_hl)
        CLUSTER.create_cluster(name="LoadAbort", model_id=self.model_dir, strategy="torch_pipeline_parallel", ctx_tokens=128, parallel_slots=1, open_net=True, coordinator_contributes=False, auto_deploy=False, fetch_model_meta=False)
        CLUSTER.register_worker(worker_id="whl", name="WHL", hw=self._hw(8192), quota={"max_ram_gb": 4}, consented=True, pipeline_port=s_hl.port, pipeline_host="127.0.0.1")
        dep_done = threading.Event()
        def _dep():
            try:
                CLUSTER.deploy_cluster(verify=False)
            except Exception:
                pass
            dep_done.set()
        threading.Thread(target=_dep, daemon=True).start()
        time.sleep(0.2)
        t_stop = time.monotonic()
        CLUSTER.stop_cluster()
        self.assertTrue(dep_done.wait(timeout=1.0))
        self.assertLess(time.monotonic() - t_stop, 1.0)
        resilience.POLICY.configure(prefill_timeout_s=0.5, decode_step_timeout_s=0.5, poll_slice_s=0.05, max_recovery_attempts=0)
        s_cpu = _ScriptedPeerServer(behavior="blackhole")
        s_cpu.start()
        self._servers.append(s_cpu)
        self._mount_single_peer("wcpu", s_cpu, coord_contrib=False)
        cpu0 = time.process_time()
        wall0 = time.monotonic()
        with self.assertRaises(resilience.InferenceFailure) as fe_cpu:
            list(CLUSTER.generate_stream([{"role": "user", "content": "hello"}], max_tokens=2))
        wall_dt = time.monotonic() - wall0
        cpu_dt = time.process_time() - cpu0
        self.assertEqual(fe_cpu.exception.code, "WORKER_TIMEOUT")
        self.assertGreaterEqual(wall_dt, 0.45)
        self.assertLess(cpu_dt, 0.25)
        CLUSTER.stop_cluster()
        class _KeepaliveSSEHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for _ in range(15):
                    try:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        time.sleep(0.05)
                    except OSError:
