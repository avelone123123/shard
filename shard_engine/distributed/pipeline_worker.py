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
