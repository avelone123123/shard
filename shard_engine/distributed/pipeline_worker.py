import copy
import gc
import importlib
import json
import os
import select
import socket
import threading
import time
try:
    import torch
    import torch.nn as nn
    from safetensors import safe_open
    from transformers import AutoConfig, DynamicCache
    from transformers.masking_utils import create_causal_mask, create_sliding_window_causal_mask
    _TORCH_PIPELINE_READY = True
except Exception:
    torch = None
    nn = None
    safe_open = None
    AutoConfig = None
    DynamicCache = None
    create_causal_mask = None
    create_sliding_window_causal_mask = None
    _TORCH_PIPELINE_READY = False
from . import pipeline_proto as proto
_NN_BASE = nn.Module if nn is not None else object
_ARCH_SPECS = {
    "llama": ("transformers.models.llama.modeling_llama", "LlamaDecoderLayer", "LlamaRotaryEmbedding", "LlamaRMSNorm"),
    "qwen2": ("transformers.models.qwen2.modeling_qwen2", "Qwen2DecoderLayer", "Qwen2RotaryEmbedding", "Qwen2RMSNorm"),
    "qwen3": ("transformers.models.qwen3.modeling_qwen3", "Qwen3DecoderLayer", "Qwen3RotaryEmbedding", "Qwen3RMSNorm"),
    "mistral": ("transformers.models.mistral.modeling_mistral", "MistralDecoderLayer", "MistralRotaryEmbedding", "MistralRMSNorm"),
    "gemma": ("transformers.models.gemma.modeling_gemma", "GemmaDecoderLayer", "GemmaRotaryEmbedding", "GemmaRMSNorm"),
    "gemma2": ("transformers.models.gemma2.modeling_gemma2", "Gemma2DecoderLayer", "Gemma2RotaryEmbedding", "Gemma2RMSNorm"),
    "phi3": ("transformers.models.phi3.modeling_phi3", "Phi3DecoderLayer", "Phi3RotaryEmbedding", "Phi3RMSNorm"),
    "gpt2": ("transformers.models.gpt2.modeling_gpt2", "GPT2Block", None, None),
}
def _resolve_arch_classes(model_type):
    spec = _ARCH_SPECS.get(str(model_type or "").strip().lower())
    if not spec:
        raise ValueError(f"Unsupported architecture for pipeline worker: {model_type}")
    mod_name, layer_cls, rope_cls, norm_cls = spec
    mod = importlib.import_module(mod_name)
    return (
        getattr(mod, layer_cls),
        getattr(mod, rope_cls) if rope_cls else None,
        getattr(mod, norm_cls) if norm_cls else None,
    )
def _remap_tensor_key(key, model_type, layer_start, layer_end, has_embed, has_lm_head, tie_word_embeddings):
    if model_type == "gpt2":
        if has_embed:
            if key == "transformer.wte.weight" or key == "wte.weight":
                return "wte.weight"
            if key == "transformer.wpe.weight" or key == "wpe.weight":
                return "wpe.weight"
        if has_lm_head:
            if key in ("transformer.ln_f.weight", "ln_f.weight"):
                return "ln_f.weight"
            if key in ("transformer.ln_f.bias", "ln_f.bias"):
                return "ln_f.bias"
            if key == "lm_head.weight":
                return "lm_head.weight"
            if tie_word_embeddings and key in ("transformer.wte.weight", "wte.weight"):
                return "lm_head.weight"
        for pfx in ("transformer.h.", "h."):
            if key.startswith(pfx):
                rest = key[len(pfx):]
                dot = rest.find(".")
                if dot > 0:
                    g_idx = int(rest[:dot])
                    if layer_start <= g_idx < layer_end:
                        return f"layers.{g_idx - layer_start}.{rest[dot + 1:]}"
        return None
    if has_embed and key in ("model.embed_tokens.weight", "embed_tokens.weight"):
        return "embed_tokens.weight"
    if has_lm_head:
        if key in ("model.norm.weight", "norm.weight"):
            return "norm.weight"
        if key in ("model.norm.bias", "norm.bias"):
            return "norm.bias"
        if key == "lm_head.weight":
            return "lm_head.weight"
        if tie_word_embeddings and key in ("model.embed_tokens.weight", "embed_tokens.weight"):
            return "lm_head.weight"
    for pfx in ("model.layers.", "layers."):
        if key.startswith(pfx):
            rest = key[len(pfx):]
            dot = rest.find(".")
            if dot > 0:
                g_idx = int(rest[:dot])
                if layer_start <= g_idx < layer_end:
                    return f"layers.{g_idx - layer_start}.{rest[dot + 1:]}"
    return None
def _download_or_locate_files(model_path_or_repo, shard_files):
    if os.path.isdir(model_path_or_repo):
        base_dir = model_path_or_repo
        resolved = []
        if shard_files:
            for fn in shard_files:
                cand = os.path.join(base_dir, fn)
                if os.path.isfile(cand):
                    resolved.append(cand)
        if not resolved:
            for fn in sorted(os.listdir(base_dir)):
                if fn.endswith(".safetensors"):
                    resolved.append(os.path.join(base_dir, fn))
        if not resolved:
            for fn in sorted(os.listdir(base_dir)):
                if fn.endswith(".bin") and "pytorch_model" in fn:
                    resolved.append(os.path.join(base_dir, fn))
        return base_dir, resolved
    from huggingface_hub import hf_hub_download
    cfg_path = hf_hub_download(model_path_or_repo, "config.json")
    base_dir = os.path.dirname(cfg_path)
    targets = list(shard_files or [])
    if not targets:
        try:
            idx_path = hf_hub_download(model_path_or_repo, "model.safetensors.index.json")
            with open(idx_path, "r", encoding="utf-8") as f:
                wm = json.load(f).get("weight_map") or {}
            targets = sorted(set(wm.values()))
        except Exception:
            targets = ["model.safetensors"]
    resolved = [hf_hub_download(model_path_or_repo, fn) for fn in targets]
    return base_dir, resolved
class PipelineStageModule(_NN_BASE):
    def __init__(self, config, model_type, layer_start, layer_end, has_embed, has_lm_head, dtype, device):
        if not _TORCH_PIPELINE_READY:
            raise RuntimeError("PyTorch, transformers, and safetensors are required for PipelineStageModule")
        super().__init__()
        self.config = config
        self.model_type = str(model_type).lower()
        self.layer_start = int(layer_start)
        self.layer_end = int(layer_end)
        self.local_count = max(0, self.layer_end - self.layer_start)
        self.has_embed = bool(has_embed)
        self.has_lm_head = bool(has_lm_head)
        self.dtype = dtype
        self.device = device
        self.stage_config = copy.deepcopy(config)
        self.stage_config.num_hidden_layers = self.local_count
        if hasattr(config, "layer_types") and isinstance(getattr(config, "layer_types"), list):
            self.stage_config.layer_types = list(config.layer_types[self.layer_start:self.layer_end])
        if hasattr(self.stage_config, "_attn_implementation") and not getattr(self.stage_config, "_attn_implementation", None):
            self.stage_config._attn_implementation = "sdpa"
        if not getattr(config, "_attn_implementation", None):
            config._attn_implementation = "sdpa"
        layer_cls, rope_cls, norm_cls = _resolve_arch_classes(self.model_type)
        if self.model_type == "gpt2":
            if self.has_embed:
                self.wte = nn.Embedding(config.vocab_size, config.hidden_size)
                self.wpe = nn.Embedding(config.max_position_embeddings, config.hidden_size)
            self.layers = nn.ModuleList([layer_cls(config, layer_idx=i) for i in range(self.local_count)])
            if self.has_lm_head:
                self.ln_f = nn.LayerNorm(config.hidden_size, eps=getattr(config, "layer_norm_epsilon", 1e-5))
                self.lm_head = nn.Linear(config.hidden_size, config.vocab_size, bias=False)
        else:
            if self.has_embed:
                self.embed_tokens = nn.Embedding(config.vocab_size, config.hidden_size, getattr(config, "pad_token_id", None))
            self.rotary_emb = rope_cls(config=config) if rope_cls is not None else None
            self.layers = nn.ModuleList([layer_cls(config, layer_idx=i) for i in range(self.local_count)])
            if self.has_lm_head:
                eps = getattr(config, "rms_norm_eps", 1e-6)
                self.norm = norm_cls(config.hidden_size, eps=eps)
                self.lm_head = nn.Linear(config.hidden_size, config.vocab_size, bias=False)
    def load_stage_weights(self, weight_files):
        tie = bool(getattr(self.config, "tie_word_embeddings", False))
        state_map = dict(self.named_parameters())
        buf_map = dict(self.named_buffers())
        loaded_keys = set()
        tied_embed_tensor = None
        for wf in weight_files:
            if wf.endswith(".safetensors"):
                with safe_open(wf, framework="pt", device="cpu") as f:
                    for key in f.keys():
                        if tie and key in ("model.embed_tokens.weight", "embed_tokens.weight", "transformer.wte.weight", "wte.weight"):
                            if self.has_lm_head and tied_embed_tensor is None:
                                tied_embed_tensor = f.get_tensor(key)
                        target = _remap_tensor_key(key, self.model_type, self.layer_start, self.layer_end, self.has_embed, self.has_lm_head, False)
                        if target and (target in state_map or target in buf_map):
                            t = f.get_tensor(key)
                            self._assign_param(state_map, buf_map, target, t)
                            loaded_keys.add(target)
            else:
                sd = torch.load(wf, map_location="cpu", weights_only=True)
                for key, t in sd.items():
                    if tie and key in ("model.embed_tokens.weight", "embed_tokens.weight", "transformer.wte.weight", "wte.weight"):
                        if self.has_lm_head and tied_embed_tensor is None:
                            tied_embed_tensor = t
                    target = _remap_tensor_key(key, self.model_type, self.layer_start, self.layer_end, self.has_embed, self.has_lm_head, False)
                    if target and (target in state_map or target in buf_map):
                        self._assign_param(state_map, buf_map, target, t)
                        loaded_keys.add(target)
                del sd
        if self.has_lm_head and "lm_head.weight" not in loaded_keys and tied_embed_tensor is not None:
            self._assign_param(state_map, buf_map, "lm_head.weight", tied_embed_tensor)
            loaded_keys.add("lm_head.weight")
        self.to(device=self.device, dtype=self.dtype)
        self.eval()
        return len(loaded_keys)
    def _assign_param(self, state_map, buf_map, target, tensor):
        if target in state_map:
            p = state_map[target]
            with torch.no_grad():
                p.copy_(tensor.to(dtype=p.dtype, device=p.device))
        elif target in buf_map:
            b = buf_map[target]
            with torch.no_grad():
                b.copy_(tensor.to(dtype=b.dtype, device=b.device))
    def new_cache(self):
        return DynamicCache(config=self.stage_config)
    def forward_stage(self, tensor_in, cache, use_cache=True, return_logits=True):
        if self.has_embed:
            input_ids = tensor_in.to(device=self.device, dtype=torch.long)
            seq_len = input_ids.shape[1]
            past_seen = cache.get_seq_length() if (use_cache and cache is not None) else 0
            position_ids = torch.arange(seq_len, device=self.device, dtype=torch.long).unsqueeze(0) + past_seen
            if self.model_type == "gpt2":
                hidden_states = self.wte(input_ids) + self.wpe(position_ids)
            else:
                hidden_states = self.embed_tokens(input_ids)
        else:
            hidden_states = tensor_in.to(device=self.device, dtype=self.dtype)
            seq_len = hidden_states.shape[1]
            past_seen = cache.get_seq_length() if (use_cache and cache is not None) else 0
            position_ids = torch.arange(seq_len, device=self.device, dtype=torch.long).unsqueeze(0) + past_seen
        if self.model_type == "gpt2":
            causal_mask = None
            if seq_len > 1:
                total_len = past_seen + seq_len
                mask = torch.full((seq_len, total_len), torch.finfo(hidden_states.dtype).min, device=self.device, dtype=hidden_states.dtype)
                mask = torch.triu(mask, diagonal=past_seen + 1)
                causal_mask = mask.unsqueeze(0).unsqueeze(0)
            for layer in self.layers:
                hidden_states = layer(hidden_states, past_key_values=cache, attention_mask=causal_mask, use_cache=use_cache)
            if self.has_lm_head:
                hidden_states = self.ln_f(hidden_states)
                logits = self.lm_head(hidden_states[:, -1:, :]).to(torch.float32)
                return logits if return_logits else torch.argmax(logits[:, -1, :], dim=-1, keepdim=True)
            return hidden_states
        mask_kwargs = {
            "config": self.stage_config,
            "inputs_embeds": hidden_states,
            "attention_mask": None,
            "past_key_values": cache,
            "position_ids": position_ids,
        }
        causal_mask_mapping = {"full_attention": create_causal_mask(**mask_kwargs)}
        has_sliding = getattr(self.stage_config, "layer_types", None) and ("sliding_attention" in self.stage_config.layer_types)
        if has_sliding:
            causal_mask_mapping["sliding_attention"] = create_sliding_window_causal_mask(**mask_kwargs)
        position_embeddings = self.rotary_emb(hidden_states, position_ids)
        layer_types = getattr(self.stage_config, "layer_types", None)
        for idx, layer in enumerate(self.layers):
            ltype = layer_types[idx] if (layer_types and idx < len(layer_types)) else "full_attention"
            hidden_states = layer(
                hidden_states,
                attention_mask=causal_mask_mapping[ltype],
                position_ids=position_ids,
                past_key_values=cache,
                use_cache=use_cache,
                position_embeddings=position_embeddings,
            )
        if self.has_lm_head:
            hidden_states = self.norm(hidden_states)
            logits = self.lm_head(hidden_states[:, -1:, :]).to(torch.float32)
            return logits if return_logits else torch.argmax(logits[:, -1, :], dim=-1, keepdim=True)
        return hidden_states
class PipelineWorkerServer:
    def __init__(self, host="127.0.0.1", port=0, auth_token="", max_threads=None, allow_cuda=True, max_vram_mb=None):
        self.host = str(host or "127.0.0.1")
        self.requested_port = int(port)
        self.auth_token = str(auth_token or "")
        self.max_threads = int(max_threads) if max_threads else None
        self.allow_cuda = bool(allow_cuda)
        self.max_vram_mb = int(max_vram_mb) if max_vram_mb else None
        self.bound_port = None
        self._sock = None
        self._stop_ev = threading.Event()
        self._accept_thread = None
        self._conn_lock = threading.Lock()
        self._conn_threads = set()
        self._active_conns = set()
        self._stage_lock = threading.RLock()
        self._stage = None
        self._kv_caches = {}
        self._requests_handled = 0
        self._bytes_rx = 0
        self._bytes_tx = 0
        self._last_compute_ms = 0.0
    def start(self):
        if self._sock is not None:
            return self.bound_port
        if self.max_threads:
            try:
                torch.set_num_threads(max(1, self.max_threads))
            except Exception:
                pass
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, self.requested_port))
        s.listen(32)
        s.settimeout(0.5)
        self._sock = s
        self.bound_port = s.getsockname()[1]
        self._stop_ev.clear()
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()
        return self.bound_port
    def stop(self):
        self._stop_ev.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        with self._conn_lock:
            conns = list(self._active_conns)
        for c in conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                c.close()
            except OSError:
                pass
        if self._accept_thread is not None and self._accept_thread.is_alive():
            self._accept_thread.join(timeout=2.0)
        with self._conn_lock:
            threads = list(self._conn_threads)
        join_deadline = time.monotonic() + 0.5
        for th in threads:
            if th.is_alive() and th is not threading.current_thread():
                th.join(timeout=max(0.01, join_deadline - time.monotonic()))
        self._unload_stage()
    def _unload_stage(self):
        with self._stage_lock:
            self._kv_caches.clear()
            self._stage = None
            if torch is not None and torch.cuda.is_available():
                torch.cuda.empty_cache()
            gc.collect()
    def stats(self):
        with self._stage_lock:
            loaded = self._stage is not None
            return {
                "running": self._sock is not None and not self._stop_ev.is_set(),
                "port": self.bound_port,
                "stage_loaded": loaded,
                "layer_start": self._stage.layer_start if loaded else None,
                "layer_end": self._stage.layer_end if loaded else None,
                "active_kv_sessions": len(self._kv_caches),
                "requests_handled": self._requests_handled,
                "bytes_rx": self._bytes_rx,
                "bytes_tx": self._bytes_tx,
                "last_compute_ms": self._last_compute_ms,
            }
    def _accept_loop(self):
        while not self._stop_ev.is_set() and self._sock is not None:
            try:
                conn, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(target=self._handle_conn, args=(conn, addr), daemon=True)
            with self._conn_lock:
                self._conn_threads.add(t)
                self._active_conns.add(conn)
            t.start()
    def _handle_conn(self, conn, _addr):
        conn.settimeout(10.0)
        owned_rids = set()
        try:
            mtype, _, payload = proto.recv_frame(conn)
            if mtype != proto.MSG_AUTH:
                proto.send_json_frame(conn, proto.MSG_AUTH_ERR, {"error": "Expected AUTH frame"})
                return
            auth_obj = json.loads(payload.decode("utf-8")) if payload else {}
            if not proto.verify_token_constant_time(auth_obj.get("token"), self.auth_token):
                proto.send_json_frame(conn, proto.MSG_AUTH_ERR, {"error": "Invalid worker token"})
                return
            proto.send_json_frame(conn, proto.MSG_AUTH_OK, {"status": "ok", "port": self.bound_port})
            conn.settimeout(60.0)
            while not self._stop_ev.is_set():
                readable, _, _ = select.select([conn], [], [], 1.0)
                if not readable:
                    continue
                mtype, _, payload = proto.recv_frame(conn)
                with self._stage_lock:
                    self._bytes_rx += len(payload)
                if mtype == proto.MSG_PING:
                    proto.send_json_frame(conn, proto.MSG_PONG, self.stats())
                elif mtype == proto.MSG_BW_PROBE:
                    proto.send_json_frame(conn, proto.MSG_BW_ACK, {"bytes": len(payload)})
                elif mtype == proto.MSG_LOAD_STAGE:
                    meta = json.loads(payload.decode("utf-8"))
                    res = self._load_stage(meta)
                    proto.send_json_frame(conn, proto.MSG_STAGE_READY, res)
                elif mtype == proto.MSG_FORWARD:
                    meta, t_in = proto.decode_tensor_payload(payload)
                    rid = str(meta.get("request_id") or "").strip()
                    if not rid or len(rid) > 128:
                        raise ValueError("Invalid or missing request_id")
                    owned_rids.add(rid)
                    t0 = time.perf_counter()
                    out_t, out_meta = self._run_forward(meta, t_in)
                    dt_ms = round((time.perf_counter() - t0) * 1000.0, 3)
                    out_meta["stage_compute_ms"] = dt_ms
                    frame_bytes = proto.encode_tensor_payload(out_meta, out_t)
                    with self._stage_lock:
                        self._last_compute_ms = dt_ms
                        self._bytes_tx += len(frame_bytes)
                    proto.send_frame(conn, proto.MSG_FORWARD_RES, frame_bytes)
                elif mtype == proto.MSG_RELEASE_KV:
                    meta = json.loads(payload.decode("utf-8")) if payload else {}
                    rid = str(meta.get("request_id") or "")
                    with self._stage_lock:
                        self._kv_caches.pop(rid, None)
                    owned_rids.discard(rid)
                    proto.send_json_frame(conn, proto.MSG_PONG, {"released": rid})
                elif mtype == proto.MSG_UNLOAD:
                    self._unload_stage()
                    proto.send_json_frame(conn, proto.MSG_PONG, {"unloaded": True})
        except (ConnectionError, EOFError, OSError):
            pass
        except Exception as e:
            try:
                proto.send_json_frame(conn, proto.MSG_ERROR, {"error": str(e)[:240]})
            except Exception:
                pass
        finally:
            if owned_rids:
                with self._stage_lock:
                    for rid in owned_rids:
                        self._kv_caches.pop(rid, None)
            with self._conn_lock:
                self._active_conns.discard(conn)
                self._conn_threads.discard(threading.current_thread())
            try:
                conn.close()
            except Exception:
                pass
    def _select_device_and_dtype(self, requested_dtype):
        use_cuda = self.allow_cuda and torch.cuda.is_available()
        if use_cuda and self.max_vram_mb is not None and self.max_vram_mb < 256:
            use_cuda = False
        dev = torch.device("cuda" if use_cuda else "cpu")
        if requested_dtype == "float16" and (use_cuda or hasattr(torch, "float16")):
            dt = torch.float16 if use_cuda else torch.float32
        elif requested_dtype == "bfloat16":
            dt = torch.bfloat16
        else:
            dt = torch.float32
        return dev, dt
    def _load_stage(self, meta):
        model_ref = str(meta["model_path_or_repo"])
        layer_start = int(meta["layer_start"])
        layer_end = int(meta["layer_end"])
        has_embed = bool(meta.get("has_embed"))
        has_lm_head = bool(meta.get("has_lm_head"))
        shard_files = list(meta.get("shard_files") or [])
        dev, dt = self._select_device_and_dtype(meta.get("dtype") or "float32")
        if isinstance(meta.get("config_dict"), dict) and meta["config_dict"].get("model_type"):
            cfg_dict = meta["config_dict"]
            base_dir, weight_files = _download_or_locate_files(model_ref, shard_files)
            cfg = AutoConfig.from_pretrained(base_dir) if os.path.isfile(os.path.join(base_dir, "config.json")) else AutoConfig.for_model(**cfg_dict)
        else:
            base_dir, weight_files = _download_or_locate_files(model_ref, shard_files)
            cfg = AutoConfig.from_pretrained(base_dir)
        model_type = str(getattr(cfg, "model_type", "") or "").lower()
        self._unload_stage()
        with self._stage_lock:
            stage = PipelineStageModule(cfg, model_type, layer_start, layer_end, has_embed, has_lm_head, dt, dev)
            n_loaded = stage.load_stage_weights(weight_files)
            self._stage = stage
            param_bytes = sum(p.numel() * p.element_size() for p in stage.parameters())
        return {
            "status": "ready",
            "layer_start": layer_start,
            "layer_end": layer_end,
            "tensors_loaded": n_loaded,
            "param_mb": round(param_bytes / (1024 * 1024), 2),
            "device": str(dev),
            "dtype": str(dt).replace("torch.", ""),
        }
    def _run_forward(self, meta, t_in):
        rid = str(meta.get("request_id") or "default")
        use_cache = bool(meta.get("use_cache", True))
        return_logits = bool(meta.get("return_logits", True))
        with self._stage_lock:
            stage = self._stage
            if stage is None:
                raise RuntimeError("Pipeline stage not loaded on worker")
            cache = None
            if use_cache:
                cache = self._kv_caches.get(rid)
                if cache is None:
                    cache = stage.new_cache()
                    self._kv_caches[rid] = cache
            with torch.inference_mode():
                out = stage.forward_stage(t_in, cache=cache, use_cache=use_cache, return_logits=return_logits)
            self._requests_handled += 1
            kv_seq = cache.get_seq_length() if cache is not None else 0
        return out, {"request_id": rid, "kv_seq_len": int(kv_seq), "seq": meta.get("seq"), "execution_id": meta.get("execution_id")}
