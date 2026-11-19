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
