import gc
import json
import os
import re
import sys
import threading
import time
from . import hardware as _hardware
from . import paths as _paths
from . import settings as _shard_settings
from . import storage as _storage
from .shard_bridge import shard_native
if "HF_HOME" not in os.environ:
    try:
        _models_root = _shard_settings.models_dir()
    except Exception:
        _models_root = None
    if not _models_root:
        _models_root = _paths.default_models_dir()
    try:
        os.makedirs(_models_root, exist_ok=True)
    except Exception:
        _models_root = _paths.default_models_dir()
        os.makedirs(_models_root, exist_ok=True)
    os.environ["HF_HOME"] = _models_root
    os.environ["TRANSFORMERS_CACHE"] = _models_root
_active_engine = None
def _progress_callback(curr, total, rate, desc):
    if _active_engine:
        _active_engine.on_tqdm_update(curr, total, rate, desc)
try:
    import tqdm
    import tqdm.auto
    _orig_tqdm = tqdm.tqdm
    class ShardTqdm(_orig_tqdm):
        def update(self, n=1):
            res = super().update(n)
            try:
                d = self.format_dict
                _progress_callback(d.get("n", 0), d.get("total", 0), d.get("rate", 0), d.get("prefix", "") or d.get("desc", ""))
            except Exception:
                pass
            return res
    tqdm.tqdm = ShardTqdm
    tqdm.auto.tqdm = ShardTqdm
except Exception:
    ShardTqdm = None
try:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, StoppingCriteria, StoppingCriteriaList, TextIteratorStreamer
    TORCH_AVAILABLE = True
    torch.set_num_threads(max(2, min(12, os.cpu_count() or 4)))
except Exception:
    torch = None
    AutoModelForCausalLM = None
    AutoTokenizer = None
    StoppingCriteria = object
    StoppingCriteriaList = list
    TextIteratorStreamer = None
    TORCH_AVAILABLE = False
try:
    from . import fit as _fit_mod
    from . import gguf_backend as gguf_mod
    from . import lan as _lan_mod
    FIT_AVAILABLE = True
except Exception:
    _fit_mod = None
    gguf_mod = None
    _lan_mod = None
    FIT_AVAILABLE = False
MODEL_LIFECYCLE_STATES = (
    "DISCOVERED",
    "COMPATIBILITY_CHECK",
    "QUEUED",
    "DOWNLOADING",
    "VERIFYING",
    "DOWNLOADED",
    "LOADING",
    "READY",
    "DOWNLOAD_FAILED",
    "VALIDATION_FAILED",
    "LOAD_FAILED",
    "CANCELLED",
)
def _fix_mojibake_utf8(text):
    if not text or not isinstance(text, str):
        return text
    if "Р" not in text and "С" not in text and "Ð" not in text and "Ñ" not in text:
        return text
    for enc in ("cp1251", "latin1"):
        try:
            raw = text.encode(enc)
            if not any(0x80 <= b <= 0xBF for b in raw):
                continue
            fixed = raw.decode("utf-8")
            if fixed != text and any("\u0400" <= ch <= "\u04ff" for ch in fixed):
                return fixed
        except Exception:
            pass
    return text
class _StreamSanitizer:
    STRIP_SEQ = (
        "<|im_start|>assistant\r\n",
        "<|im_start|>assistant\n",
        "<|im_start|>assistant",
        "<|im_sep|>\r\n",
        "<|im_sep|>\n",
        "<|im_sep|>",
    )
    STOP_SEQ = (
