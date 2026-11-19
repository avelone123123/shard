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
