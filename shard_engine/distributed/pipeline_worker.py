import copy
import gc
import importlib
import json
import os
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
