"""
GGUF backend for shard: llama.cpp as an isolated subprocess.

Why subprocess: transformers loads unquantized weights in-process, so a model
bigger than RAM takes the whole app down (OOM kills the process). llama-server
runs in its own OS process with mmap + partial GPU offload (-ngl), and speaks
the OpenAI chat-completions API on localhost.

This module never touches torch and never downloads model weights without being
asked. Quant discovery/download use the standard HF cache (resume-safe).
"""
import glob
import json
import os
import re
import shutil
import socket
import subprocess
import time
import zipfile

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_RUNTIME_DIR = os.path.join(os.path.expanduser("~"), ".shard", "runtime")
_LLAMA_TAG = "b12110"
_ASSET_CPU = f"llama-{_LLAMA_TAG}-bin-win-cpu-x64.zip"
_ASSET_CUDA = f"llama-{_LLAMA_TAG}-bin-win-cuda-12.4-x64.zip"
_ASSET_CUDART = f"cudart-llama-bin-win-cuda-12.4-x64.zip"
_RELEASE_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{_LLAMA_TAG}"

# canonical quant name -> bits per weight
QUANT_BPW = {
    "IQ1_S": 1.6, "IQ1_M": 1.9,
    "IQ2_XXS": 2.1, "IQ2_XS": 2.4, "IQ2_S": 2.6, "IQ2_M": 2.8, "Q2_K": 3.05, "Q2_K_S": 2.9,
    "IQ3_XXS": 3.2, "IQ3_XS": 3.5, "IQ3_S": 3.6, "Q3_K_S": 3.5, "Q3_K_M": 3.9, "Q3_K_L": 4.1,
    "IQ4_XS": 4.4, "IQ4_NL": 4.5, "Q4_K_S": 4.4, "Q4_K_M": 4.85, "Q5_K_S": 5.4, "Q5_K_M": 5.7,
    "Q6_K": 6.6, "Q8_0": 8.5, "F16": 16.0, "BF16": 16.0,
}

# quant suffix is REQUIRED: "Qwen2.5" must not parse as quant "Q2".
# After the digit we accept _K variants, _0, and generic IQ-style suffixes
# (IQ4_XS / IQ4_NL / IQ1_S ...). "q4" alone (no underscore) stays rejected.
_QUANT_RE = re.compile(
    r"(?i)(?:^|[._\- ])(i?q[1-8](?:_k(?:_[a-z0-9]+)?|_[a-z0-9]{1,4}))(?=[._\- ]|$)")
_PART_RE = re.compile(r"(?i)(.*)-00001-of-(\d{3,})\.gguf$")
# any part suffix (-00001, -00002, ...); first parts also match _PART_RE above
_MULTIPART_RE = re.compile(r"(?i)-\d{5}-of-\d{3,}\.gguf$")

_repo_cache = {}


# ---------------------------------------------------------------------------
# quant helpers
# ---------------------------------------------------------------------------
def normalize_quant(name):
    if not name:
        return None
    n = name.strip().upper().replace(" ", "_")
    if n in QUANT_BPW:
        return n
    # common sloppy spellings: Q4_K_M → Q4_K_M, q4-k-m, Q4KM
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


# ---------------------------------------------------------------------------
# GGUF repo discovery
# ---------------------------------------------------------------------------
def _model_api_url(repo_id):
    return f"https://huggingface.co/api/models/{repo_id}"


def _repo_exists(repo_id, timeout=4.0):
    try:
        import requests
        r = requests.get(_model_api_url(repo_id), timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def find_gguf_repo(model_id, timeout=4.0):
    """Locate a ready-made GGUF repo for a HF model. Returns repo_id or None."""
    if not model_id:
        return None
    if model_id.lower().endswith("-gguf") or ".gguf" in model_id.lower():
        return model_id
    if model_id in _repo_cache:
        return _repo_cache[model_id]
    author = model_id.split("/")[0] if "/" in model_id else ""
    name = model_id.split("/")[-1]
    name_clean = re.sub(r"(?i)[-_]?instruct$", "", name)
    candidates = [
        f"{author}/{name}-GGUF" if author else f"{name}-GGUF",
        f"gg-hf-g/{name_clean}-GGUF",
        f"unsloth/{name_clean}-GGUF",
        f"MaziyarPanahi/{name_clean}-GGUF",
    ]
    found = None
    for cand in candidates:
        if cand and _repo_exists(cand, timeout):
            found = cand
            break
    if not found:
        found = _search_gguf_repo(name_clean, timeout)
    _repo_cache[model_id] = found
    return found


def _search_gguf_repo(name_clean, timeout=5.0):
    try:
        import requests
        r = requests.get(
            "https://huggingface.co/api/models",
            params={"search": f"{name_clean} gguf", "sort": "downloads",
                    "direction": -1, "limit": 20},
            timeout=timeout)
        if r.status_code != 200:
            return None
        low = name_clean.lower()
        for item in r.json():
            rid = item.get("id", "")
            if not rid.lower().endswith("-gguf"):
                continue
            if low in rid.lower() or low.replace("-", "") in rid.lower().replace("-", ""):
                # prefer gg-hf-g / original author over third-party mirrors
                return rid
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# file listing / selection
# ---------------------------------------------------------------------------
def list_gguf_files(repo_id, timeout=8.0):
    """[(path, size_bytes)] of .gguf files in a repo, or [] on failure."""
    try:
        import requests
        r = requests.get(
            f"https://huggingface.co/api/models/{repo_id}/tree/main",
            params={"recursive": "true"}, timeout=timeout)
        if r.status_code != 200:
            return []
        out = []
        for e in r.json():
            p = e.get("path", "")
            if p.lower().endswith(".gguf") and not p.endswith("/"):
                out.append((p, int(e.get("size", 0) or 0)))
        return out
    except Exception:
        return []


def select_gguf_file(files, wanted_quant=None, prefer_max_bpw=None):
    """Pick the best gguf file(s) from a listing.

    Returns dict: {path, size_bytes, quant, multipart, parts} or None.
    Multipart models are grouped: representative is the -00001 file, size is
    the sum of all parts (llama.cpp loads them from the same snapshot dir).
    """
    if not files:
        return None
    groups = {}
    for path, size in files:
        m = _PART_RE.match(os.path.basename(path))
        if m:
            key = f"{os.path.dirname(path)}/{m.group(1)}".lstrip("/")
            g = groups.setdefault(key, {"parts": [], "total": 0})
            g["parts"].append(path)
            g["total"] += size
        else:
            if _MULTIPART_RE.search(os.path.basename(path)):
                # -00002 / -00003 ... parts: attach to the group of their
                # -00001 sibling (same dir + same base name), so size_bytes
                # covers the whole model and download fetches every part.
                pm = re.match(r"(?i)(.*)-\d{5}-of-\d{3,}\.gguf$", os.path.basename(path))
                if pm:
                    key = f"{os.path.dirname(path)}/{pm.group(1)}".lstrip("/")
                    g = groups.get(key)
                    if g is not None:
                        g["parts"].append(path)
                        g["total"] += size
                continue
            groups[path] = {"parts": [path], "total": size}
    cands = []
    for key, g in groups.items():
        rep = g["parts"][0]
        q = parse_quant_from_name(rep)
        if not q:
            continue
        cands.append({
            "path": rep,
            "size_bytes": g["total"],
            "quant": q,
            "multipart": len(g["parts"]) > 1,
            "parts": g["parts"],
        })
    if not cands:
        return None
    if wanted_quant:
        w = normalize_quant(wanted_quant)
        if w:
            exact = [c for c in cands if c["quant"] == w]
            if exact:
                return _prefer_single(exact)
            # fall back to the next SMALLER quant (safety over quality)
            w_bpw = QUANT_BPW[w]
            smaller = [c for c in cands if QUANT_BPW.get(c["quant"], 0) < w_bpw]
            if smaller:
                return _prefer_single([max(smaller, key=lambda c: QUANT_BPW.get(c["quant"], 0))])
            smaller_or_equal = [c for c in cands if QUANT_BPW.get(c["quant"], 0) <= w_bpw + 0.01]
            if smaller_or_equal:
                return _prefer_single([max(smaller_or_equal, key=lambda c: QUANT_BPW.get(c["quant"], 0))])
    if prefer_max_bpw is not None:
        fits = [c for c in cands if QUANT_BPW.get(c["quant"], 0) <= prefer_max_bpw + 0.01]
        if fits:
            return _prefer_single([max(fits, key=lambda c: QUANT_BPW.get(c["quant"], 0))])
    return _prefer_single(cands)


def _prefer_single(cands):
    if len(cands) == 1:
        return cands[0]
    best = max(cands, key=lambda c: QUANT_BPW.get(c["quant"], 0))
    singles = [c for c in cands if not c["multipart"] and c["quant"] == best["quant"]]
    return singles[0] if singles else best


# ---------------------------------------------------------------------------
# download (resume-safe via HF cache)
# ---------------------------------------------------------------------------
def download_gguf(repo_id, selection):
    """Download selected gguf file(s) into the HF cache. Returns local path to
    the first file (with original filename, needed for multipart loading)."""
    from huggingface_hub import snapshot_download
    patterns = list(selection["parts"])
    snap = snapshot_download(repo_id, allow_patterns=patterns)
    for sub in selection["parts"]:
        cand = os.path.join(snap, *sub.split("/"))
        if os.path.isfile(cand):
            return cand
    # last resort: search
    hits = glob.glob(os.path.join(snap, "**", "*.gguf"), recursive=True)
    if hits:
        return hits[0]
    raise RuntimeError("GGUF скачан, но файл не найден в кэше")


# ---------------------------------------------------------------------------
# llama.cpp runtime
# ---------------------------------------------------------------------------
def find_llama_server():
    cand = os.environ.get("SHARD_LLAMA_SERVER")
    if cand and os.path.isfile(cand):
        return cand
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (os.getcwd(), here):
        p = os.path.join(base, "native", "llama-server.exe")
        if os.path.isfile(p):
            return p
    which = shutil.which("llama-server")
    if which:
        return which
    p = os.path.join(_RUNTIME_DIR, "llama-server.exe")
    if os.path.isfile(p):
        return p
    hits = glob.glob(os.path.join(_RUNTIME_DIR, "**", "llama-server.exe"), recursive=True)
    return hits[0] if hits else None


def _nvidia_present():
    try:
        r = subprocess.run(["nvidia-smi"], capture_output=True, timeout=5,
                           creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0)
        return r.returncode == 0
    except Exception:
        return False


def _download_file(url, dest, progress_cb=None, chunk=1024 * 512):
    import requests
    r = requests.get(url, stream=True, timeout=30)
    r.raise_for_status()
    total = int(r.headers.get("content-length", 0) or 0)
    got = 0
    t0 = time.time()
    tmp = dest + ".part"
    with open(tmp, "wb") as f:
        for blk in r.iter_content(chunk_size=chunk):
            f.write(blk)
            got += len(blk)
            if progress_cb and total:
                speed = got / max(0.5, time.time() - t0)
                progress_cb(got, total, speed)
    os.replace(tmp, dest)
    return dest


def ensure_llama_server(progress_cb=None):
    """Find llama-server.exe or download the known-good nightly build.

    CPU build by default (~50 MB). SHARD_LLAMA_CUDA=1 (+ nvidia GPU) switches
    to the CUDA build so -ngl actually offloads layers to VRAM.
    """
    found = find_llama_server()
    if found:
        return found
    use_cuda = os.environ.get("SHARD_LLAMA_CUDA", "0") == "1" and _nvidia_present()
    assets = [_ASSET_CUDART, _ASSET_CUDA] if use_cuda else [_ASSET_CPU]
    os.makedirs(_RUNTIME_DIR, exist_ok=True)
    for asset in assets:
        dest = os.path.join(_RUNTIME_DIR, asset)
        if not os.path.isfile(dest):
            if progress_cb:
                progress_cb(0, 0, 0)
            _download_file(f"{_RELEASE_URL}/{asset}", dest, progress_cb)
        with zipfile.ZipFile(dest) as z:
            z.extractall(_RUNTIME_DIR)
    found = find_llama_server()
    if not found:
        raise RuntimeError("llama-server.exe не найден после установки (~/.shard/runtime)")
    return found


# ---------------------------------------------------------------------------
# process planning
# ---------------------------------------------------------------------------
def compute_ngl(verdict, fit_ngl=0):
    if verdict == "GPU":
        return 999
    if verdict == "HYBRID":
        return max(0, int(fit_ngl))
    return 0


def default_threads():
    cores = os.cpu_count() or 4
    return max(2, cores // 2)


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------------------
# backend
# ---------------------------------------------------------------------------
class LlamaBackend:
    """Owns one llama-server subprocess. One model at a time."""

    def __init__(self):
        self.proc = None
        self.port = None
        self.model_id = None
        self.gguf_path = None
        self.started_at = 0.0
        self.ctx_tokens = 4096
        self.ngl = 0

    # -- lifecycle ---------------------------------------------------------
    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, gguf_path, ctx_tokens, ngl, threads, model_id, server_path=None,
              wait_timeout=180.0, on_wait=None):
        self.stop()
        server_path = server_path or find_llama_server()
        if not server_path:
            server_path = ensure_llama_server()
        self.gguf_path = gguf_path
        self.model_id = model_id
        self.ctx_tokens = int(ctx_tokens)
        self.ngl = int(ngl)
        self.port = _free_port()
        cmd = [
            server_path,
            "-m", gguf_path,
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "-c", str(self.ctx_tokens),
            "-ngl", str(self.ngl),
            "-t", str(threads),
            "--parallel", "1",
        ]
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = _CREATE_NO_WINDOW
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            **kw)
        self.started_at = time.time()
        deadline = time.time() + wait_timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                tail = ""
                try:
                    tail = (self.proc.stderr.read() or b"").decode("utf-8", "ignore")[-400:]
                except Exception:
                    pass
                code = self.proc.returncode
                self.proc = None
                raise RuntimeError(f"llama-server завершился (код {code}): {tail[:200]}")
            if self._health_ok():
                return True
            if on_wait:
                on_wait(time.time() - self.started_at, wait_timeout)
            time.sleep(0.5)
        self.stop()
        raise RuntimeError("llama-server не ответил /health вовремя")

    def _health_ok(self):
        if self.port is None:
            return False
        try:
            import requests
            r = requests.get(f"http://127.0.0.1:{self.port}/health", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        return False

    def stop(self):
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        self.port = None
        self.gguf_path = None
        self.model_id = None

    # -- inference ---------------------------------------------------------
    def chat_stream(self, messages, max_tokens=512, temperature=0.7, cancel_check=None):
        """Yield text deltas, then a final ('usage', dict) marker."""
        if not self.is_running():
            raise RuntimeError("GGUF backend не запущен")
        url = f"http://127.0.0.1:{self.port}/v1/chat/completions"
        payload = {
            "messages": messages,
            "max_tokens": max(16, min(int(max_tokens), 4096)),
            "temperature": max(0.0, min(1.5, float(temperature))),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        import requests
        usage = None
        try:
            with requests.post(url, json=payload, stream=True, timeout=(5, 600)) as r:
                if r.status_code == 400 and "stream_options" in (r.text or "")[:400]:
                    payload.pop("stream_options", None)
                    r.close()
                    r = requests.post(url, json=payload, stream=True, timeout=(5, 600))
                r.raise_for_status()
                for line in r.iter_lines(decode_unicode=True):
                    if cancel_check and cancel_check():
                        break
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        continue
                    choices = obj.get("choices") or []
                    if choices:
                        delta = choices[0].get("delta") or {}
                        tok = delta.get("content")
                        if tok:
                            yield ("delta", tok)
                    if obj.get("usage"):
                        usage = obj["usage"]
        except requests.RequestException as e:
            yield ("error", str(e))
            return
        yield ("usage", usage)

    def info(self):
        return {
            "backend": "gguf",
            "model_id": self.model_id,
            "gguf_path": self.gguf_path,
            "port": self.port,
            "ngl": self.ngl,
            "ctx_tokens": self.ctx_tokens,
            "running": self.is_running(),
        }
