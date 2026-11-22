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
