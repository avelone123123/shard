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
