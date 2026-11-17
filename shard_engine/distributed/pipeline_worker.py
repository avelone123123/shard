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
