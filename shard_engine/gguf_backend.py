import glob
import json
import os
import platform
import re
import shutil
import socket
import struct
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path
from . import paths
from . import settings
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_RUNTIME_DIR = paths.runtime_dir()
_LLAMA_TAG = "b12110"
_ASSET_CPU = f"llama-{_LLAMA_TAG}-bin-win-cpu-x64.zip"
_ASSET_CUDA = f"llama-{_LLAMA_TAG}-bin-win-cuda-12.4-x64.zip"
_ASSET_CUDART = "cudart-llama-bin-win-cuda-12.4-x64.zip"
_ASSET_LINUX_CPU = f"llama-{_LLAMA_TAG}-bin-ubuntu-x64.zip"
_ASSET_MAC_ARM = f"llama-{_LLAMA_TAG}-bin-macos-arm64.zip"
_ASSET_MAC_X64 = f"llama-{_LLAMA_TAG}-bin-macos-x64.zip"
_RELEASE_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{_LLAMA_TAG}"
QUANT_BPW = {
    "IQ1_S": 1.6, "IQ1_M": 1.9,
    "IQ2_XXS": 2.1, "IQ2_XS": 2.4, "IQ2_S": 2.6, "IQ2_M": 2.8, "Q2_K_S": 2.9, "Q2_K": 3.05, "Q2_K_L": 3.15,
    "IQ3_XXS": 3.2, "IQ3_XS": 3.5, "Q3_K_S": 3.5, "IQ3_S": 3.6, "IQ3_M": 3.7, "Q3_K_M": 3.9, "Q3_K_L": 4.1, "Q3_K_XL": 4.2,
    "IQ4_XS": 4.4, "Q4_K_S": 4.4, "IQ4_NL": 4.5, "Q4_0": 4.5, "Q4_1": 5.0, "Q4_K": 4.8, "Q4_K_M": 4.85, "Q4_K_L": 4.95,
    "Q5_0": 5.5, "Q5_1": 6.0, "Q5_K_S": 5.4, "Q5_K": 5.65, "Q5_K_M": 5.7, "Q5_K_L": 5.8,
    "Q6_K": 6.6, "Q6_K_L": 6.7, "Q8_0": 8.5, "F16": 16.0, "BF16": 16.0, "F32": 32.0,
}
_QUANT_RE = re.compile(
    r"(?i)(?:^|[._\- ])(i?q[1-8](?:_k(?:_[a-z0-9]+)?|_[a-z0-9]{1,4}))(?=[._\- ]|$)")
_PART_RE = re.compile(r"(?i)(.*)-00001-of-(\d{3,})\.gguf$")
_MULTIPART_RE = re.compile(r"(?i)-\d{5}-of-\d{3,}\.gguf$")
_repo_cache = {}
CHAT_STOP_TOKENS = ["<|im_end|>", "<|endoftext|>", "<|im_start|>", "<|im_sep|>"]
def normalize_quant(name):
    if not name:
        return None
    n = name.strip().upper().replace(" ", "_")
    if n in QUANT_BPW:
        return n
    n2 = re.sub(r"[.\-]", "_", n)
    n2 = re.sub(r"Q(\d)K(M|S|L)", r"Q\1_K_\2", n2)
    n2 = re.sub(r"Q(\d)_?0", r"Q\1_0", n2)
    if n2 in QUANT_BPW:
        return n2
    return None
def parse_quant_from_name(path):
    base = os.path.basename(path)
    m = _QUANT_RE.search(base)
    if m:
        q = normalize_quant(m.group(1))
        if q:
            return q
    if re.search(r"(?i)(?:[._\- ])(?:f16|bf16)(?:[._\- ]|$)", base):
        return "F16"
    return None
def quant_rank(name):
    q = normalize_quant(name) or name
    return QUANT_BPW.get(q, 0.0)
def validate_gguf_file(path, expected_size=0):
    if not path or not os.path.isfile(path):
        return {"valid": False, "reason": "file_not_found", "size_bytes": 0}
    try:
        sz = os.path.getsize(path)
        if sz <= 0:
            return {"valid": False, "reason": "empty_file", "size_bytes": 0}
        if expected_size and int(expected_size) > 0 and sz < int(expected_size):
            return {"valid": False, "reason": f"incomplete_size ({sz}/{expected_size})", "size_bytes": sz}
        with open(path, "rb") as f:
            hdr = f.read(8)
        if len(hdr) < 4 or hdr[:4] != b"GGUF":
            return {"valid": False, "reason": "invalid_gguf_magic", "size_bytes": sz}
        ver = 3
        if len(hdr) >= 8 and hdr[4:8] in (b"\x01\x00\x00\x00", b"\x02\x00\x00\x00", b"\x03\x00\x00\x00"):
            ver = struct.unpack("<I", hdr[4:8])[0]
        return {"valid": True, "version": ver, "size_bytes": sz}
    except Exception as e:
        return {"valid": False, "reason": str(e), "size_bytes": 0}
def _model_api_url(repo_id):
    return f"https://huggingface.co/api/models/{repo_id}"
def _repo_exists(repo_id, timeout=4.0):
    try:
        import requests
        r = requests.get(_model_api_url(repo_id), timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False
def _repo_has_gguf(repo_id, timeout=4.0):
    try:
        import requests
        r = requests.get(_model_api_url(repo_id), timeout=timeout)
        if r.status_code != 200:
            return False
        data = r.json() if hasattr(r, "json") else {}
        if isinstance(data, dict):
            siblings = data.get("siblings") or []
            if siblings:
                return any(str(s.get("rfilename", "")).lower().endswith(".gguf") for s in siblings if isinstance(s, dict))
            tags = [str(t).lower() for t in (data.get("tags") or [])]
            if "gguf" in tags:
                return True
        files = list_gguf_files(repo_id, timeout=timeout)
        if files:
            return True
