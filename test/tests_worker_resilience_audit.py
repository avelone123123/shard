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
