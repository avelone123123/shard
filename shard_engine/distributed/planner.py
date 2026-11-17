import json
import math
import os
from .. import fit as _fit
from .. import gguf_backend as _gguf
from .. import hardware as _hardware
from . import capabilities as _cap
SUPPORTED_TORCH_PIPELINE_TYPES = frozenset({
    "llama",
    "qwen2",
    "qwen3",
    "mistral",
    "gemma",
    "gemma2",
    "phi3",
    "gpt2",
})
_MB = 1024 * 1024
def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None
def _resolve_local_hf_dir(model_id):
    if not model_id:
        return None
    raw = str(model_id).strip()
    if os.path.isdir(raw) and os.path.isfile(os.path.join(raw, "config.json")):
        return raw
    for hub_dir in _gguf.all_hub_dirs():
        entry = f"models--{raw.replace('/', '--')}"
        snap_root = os.path.join(hub_dir, entry, "snapshots")
        if os.path.isdir(snap_root):
            try:
                for sub in sorted(os.listdir(snap_root)):
                    cand = os.path.join(snap_root, sub)
                    if os.path.isfile(os.path.join(cand, "config.json")):
                        return cand
            except Exception:
                pass
    return None
def _read_safetensors_index(local_dir):
    if not local_dir:
        return None
    idx_path = os.path.join(local_dir, "model.safetensors.index.json")
    if os.path.isfile(idx_path):
        data = _read_json(idx_path)
        if isinstance(data, dict) and isinstance(data.get("weight_map"), dict):
            return data["weight_map"]
    single = os.path.join(local_dir, "model.safetensors")
    if os.path.isfile(single):
        try:
            from safetensors import safe_open
            with safe_open(single, framework="pt", device="cpu") as f:
                return {k: "model.safetensors" for k in f.keys()}
        except Exception:
            pass
    return None
def _layer_prefix_for_type(model_type):
    if model_type == "gpt2":
        return "transformer.h."
    return "model.layers."
def _shard_files_for_stage(weight_map, model_type, layer_start, layer_end, has_embed, has_lm_head):
    if not weight_map:
        return []
    prefix = _layer_prefix_for_type(model_type)
    files = set()
    for tensor_name, shard_file in weight_map.items():
        if has_embed and (tensor_name.startswith("model.embed_tokens") or tensor_name.startswith("transformer.wte") or tensor_name.startswith("transformer.wpe")):
            files.add(shard_file)
            continue
