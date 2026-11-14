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
        return repo_id.lower().endswith("-gguf")
    except Exception:
        return False
def all_hub_dirs():
    dirs = []
    candidates = [
        os.environ.get("HF_HOME"),
        settings.models_dir(),
        paths.default_models_dir(),
        os.path.join(os.path.expanduser("~"), ".shard", "models"),
        os.path.join(os.path.expanduser("~"), ".cache", "huggingface"),
        str(paths.data_dir() / "models"),
    ]
    for d in candidates:
        if d:
            h = os.path.join(d, "hub") if not str(d).endswith("hub") else d
            norm = os.path.normcase(os.path.abspath(h))
            if os.path.isdir(h) and norm not in [os.path.normcase(os.path.abspath(x)) for x in dirs]:
                dirs.append(h)
    return dirs
def find_local_gguf(model_id, wanted_quant=None):
    if not model_id:
        return None
    raw = str(model_id).strip()
    if os.path.isfile(raw) and raw.lower().endswith(".gguf") and os.path.getsize(raw) > 0:
        v = validate_gguf_file(raw)
        if v.get("valid"):
            return {
                "path": os.path.abspath(raw),
                "size_bytes": os.path.getsize(raw),
                "quant": parse_quant_from_name(raw) or wanted_quant or "CUSTOM",
                "repo_id": "local",
                "multipart": False,
                "parts": [os.path.basename(raw)],
            }
    bare = raw.split("/")[-1].lower()
    clean = re.sub(r"(?i)[-_]?(instruct)?[-_]?gguf$", "", bare)
    name_core = re.sub(r"(?i)[-_]?instruct$", "", bare)
    exact_candidates = []
    fuzzy_candidates = []
    for hub_dir in all_hub_dirs():
        if not os.path.isdir(hub_dir):
            continue
        try:
            entries = os.listdir(hub_dir)
        except Exception:
            continue
        for entry in entries:
            low = entry.lower()
            if not low.startswith("models--"):
                continue
            repo_id = entry.replace("models--", "").replace("--", "/")
            repo_tail = repo_id.split("/")[-1].lower()
            repo_tail_no_gguf = re.sub(r"(?i)[-_]?gguf$", "", repo_tail)
            is_exact = (repo_id.lower() == raw.lower() or repo_id.lower() == f"{raw.lower()}-gguf" or repo_tail == bare or repo_tail_no_gguf == bare or repo_tail == f"{bare}-gguf")
            if not is_exact and not (bare in low or clean in low or name_core in low):
                continue
            snap_dir = os.path.join(hub_dir, entry, "snapshots")
            if not os.path.isdir(snap_dir):
                continue
            try:
                for r, _, fns in os.walk(snap_dir):
                    for fn in fns:
                        if fn.lower().endswith(".gguf") and not fn.endswith(".incomplete") and not fn.endswith(".part"):
                            fp = os.path.join(r, fn)
                            try:
                                sz = os.path.getsize(fp)
                                if sz > 0 and validate_gguf_file(fp).get("valid"):
                                    q = parse_quant_from_name(fn) or "CUSTOM"
                                    rec = {
                                        "path": fp,
                                        "size_bytes": sz,
                                        "quant": q,
                                        "repo_id": repo_id,
                                        "multipart": False,
                                        "parts": [fn],
                                    }
                                    if is_exact:
                                        exact_candidates.append(rec)
                                    else:
                                        fuzzy_candidates.append(rec)
                            except Exception:
                                pass
            except Exception:
                pass
    candidates = exact_candidates if exact_candidates else fuzzy_candidates
    if not candidates:
        return None
    if wanted_quant:
        wq_norm = normalize_quant(wanted_quant) or wanted_quant.upper()
        for c in candidates:
            if c["quant"] and (c["quant"].upper() == wq_norm or c["quant"].upper() == wanted_quant.upper()):
                return c
    return max(candidates, key=lambda c: (QUANT_BPW.get(c["quant"], 0), c["size_bytes"]))
def find_gguf_repo(model_id, timeout=4.0):
    if not model_id:
        return None
    loc = find_local_gguf(model_id)
    if loc and loc.get("repo_id") and loc["repo_id"] != "local":
        return loc["repo_id"]
    if model_id in _repo_cache:
        return _repo_cache[model_id]
    if "/" in model_id:
        if model_id.lower().endswith("-gguf") or ".gguf" in model_id.lower():
            if _repo_exists(model_id, timeout):
                _repo_cache[model_id] = model_id
                return model_id
    author = model_id.split("/")[0] if "/" in model_id else ""
    name = model_id.split("/")[-1]
    name_clean = re.sub(r"(?i)[-_]?instruct$", "", name)
    name_no_gguf = re.sub(r"(?i)[-_]?gguf$", "", name)
    candidates = [
        f"{author}/{name_no_gguf}-GGUF" if author else None,
        f"Qwen/{name_no_gguf}-GGUF" if "qwen" in name.lower() else None,
        f"bartowski/{name_no_gguf}-GGUF",
        f"unsloth/{name_no_gguf}-GGUF",
        f"MaziyarPanahi/{name_no_gguf}-GGUF",
        f"TheBloke/{name_no_gguf}-GGUF",
        f"gg-hf-g/{name_no_gguf}-GGUF",
        f"bartowski/{name_clean}-GGUF" if name_clean != name_no_gguf else None,
        f"unsloth/{name_clean}-GGUF" if name_clean != name_no_gguf else None,
        f"MaziyarPanahi/{name_clean}-GGUF" if name_clean != name_no_gguf else None,
        f"gg-hf-g/{name_clean}-GGUF" if name_clean != name_no_gguf else None,
        f"{author}/{name}" if author and author != name else None,
    ]
    seen = set()
    found = None
    for cand in candidates:
        if not cand or cand.lower() in seen:
            continue
        seen.add(cand.lower())
        if cand.lower().endswith("-gguf"):
            if _repo_exists(cand, timeout):
                found = cand
                break
        else:
            if _repo_has_gguf(cand, timeout):
                found = cand
                break
    if not found:
        found = _search_gguf_repo(name_no_gguf, timeout) or _search_gguf_repo(name_clean, timeout)
    if found:
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
                return rid
        return None
    except Exception:
        return None
def list_gguf_files(repo_id, timeout=8.0):
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
def list_available_quants(repo_id, timeout=8.0):
    gguf_repo = find_gguf_repo(repo_id, timeout=timeout) or repo_id
    files = list_gguf_files(gguf_repo, timeout=timeout)
    if not files:
        return {"repo_id": gguf_repo, "quants": []}
    groups = {}
    for path, size in files:
        m = _PART_RE.match(os.path.basename(path))
        if m:
            key = f"{os.path.dirname(path)}/{m.group(1)}".lstrip("/")
            g = groups.setdefault(key, {"parts": [], "total": 0})
            g["parts"].append(path)
            g["total"] += size
        elif _MULTIPART_RE.search(os.path.basename(path)):
            pm = re.match(r"(?i)(.*)-\d{5}-of-\d{3,}\.gguf$", os.path.basename(path))
            if pm:
                key = f"{os.path.dirname(path)}/{pm.group(1)}".lstrip("/")
                g = groups.get(key)
                if g is not None:
                    g["parts"].append(path)
                    g["total"] += size
        else:
            groups[path] = {"parts": [path], "total": size}
    quants = []
    for _, g in groups.items():
        if not g["parts"]:
            continue
        rep = g["parts"][0]
        q = parse_quant_from_name(rep)
        if not q:
            continue
        quants.append({
            "quant": q,
            "bpw": QUANT_BPW.get(q, 0.0),
            "size_bytes": g["total"],
            "size_gb": round(g["total"] / (1024 ** 3), 2),
            "path": rep,
            "multipart": len(g["parts"]) > 1,
            "parts": g["parts"],
        })
    quants.sort(key=lambda x: x["bpw"], reverse=True)
    return {"repo_id": gguf_repo, "quants": quants}
def select_gguf_file(files, wanted_quant=None, prefer_max_bpw=None):
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
def download_gguf(repo_id, selection, progress_cb=None, cancel_check=None):
    loc = find_local_gguf(repo_id, selection.get("quant") if isinstance(selection, dict) else None)
    if loc and os.path.isfile(loc.get("path", "")) and loc.get("size_bytes", 0) > 0:
        return loc["path"]
    canonical_repo = find_gguf_repo(repo_id) or repo_id
    hf_home = settings.models_dir() or os.environ.get("HF_HOME") or paths.default_models_dir()
    snap = os.path.join(hf_home, "hub", f"models--{canonical_repo.replace('/', '--')}", "snapshots", "main")
    parts = selection["parts"] if (isinstance(selection, dict) and "parts" in selection) else [selection.get("path", "model.gguf") if isinstance(selection, dict) else "model.gguf"]
    expected_total = int(selection.get("size_bytes", 0) or 0) if isinstance(selection, dict) else 0
    first_path = None
    completed_bytes = 0
    for sub in parts:
        if cancel_check and cancel_check():
            raise RuntimeError("Загрузка отменена пользователем")
        dest = os.path.join(snap, *sub.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.isfile(dest) and validate_gguf_file(dest).get("valid"):
            completed_bytes += os.path.getsize(dest)
            if first_path is None:
                first_path = dest
            continue
        url = f"https://huggingface.co/{canonical_repo}/resolve/main/{sub}"
        def _part_cb(got_part, total_part, speed_bps, cur_name=os.path.basename(sub)):
            if progress_cb:
                tot = expected_total if expected_total > 0 else (completed_bytes + total_part)
                progress_cb(completed_bytes + got_part, tot, speed_bps, cur_name)
        _download_file(url, dest, progress_cb=_part_cb, cancel_check=cancel_check)
        val = validate_gguf_file(dest)
        if not val.get("valid"):
            try:
                os.remove(dest)
            except Exception:
                pass
            raise RuntimeError(f"Проверка GGUF-файла не пройдена ({os.path.basename(dest)}): {val.get('reason')}")
        completed_bytes += os.path.getsize(dest)
        if first_path is None:
            first_path = dest
    if first_path and os.path.isfile(first_path):
        return first_path
    raise RuntimeError("GGUF скачан, но файл не найден в кэше")
def _runtime_dirs():
    dirs = []
    home_rt = os.path.join(os.path.expanduser("~"), ".shard", "runtime")
    for d in (_RUNTIME_DIR, paths.runtime_dir(), home_rt):
        if d and os.path.normcase(os.path.abspath(d)) not in [os.path.normcase(os.path.abspath(x)) for x in dirs]:
            dirs.append(d)
    return dirs
def _server_bin_name():
    return "llama-server.exe" if os.name == "nt" else "llama-server"
def find_llama_server():
    cand = os.environ.get("SHARD_LLAMA_SERVER")
    if cand and os.path.isfile(cand):
        return cand
    exe_name = _server_bin_name()
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (os.getcwd(), here, os.path.dirname(here)):
        p = os.path.join(base, "native", exe_name)
        if os.path.isfile(p):
            return p
    which = shutil.which("llama-server") or shutil.which(exe_name)
    if which:
        return which
    for rt_dir in _runtime_dirs():
        p = os.path.join(rt_dir, exe_name)
        if os.path.isfile(p):
            return p
        hits = glob.glob(os.path.join(rt_dir, "**", exe_name), recursive=True)
        if hits:
            return hits[0]
    return None
def _nvidia_present():
    try:
        r = subprocess.run(["nvidia-smi"], capture_output=True, timeout=5,
                           creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0)
        return r.returncode == 0
    except Exception:
        return False
def _download_file(url, dest, progress_cb=None, chunk=1024 * 512, cancel_check=None):
    import requests
    tmp = dest + ".part"
    resume_bytes = 0
    if os.path.isfile(tmp):
        try:
            resume_bytes = os.path.getsize(tmp)
        except Exception:
            resume_bytes = 0
    headers = {}
    hf_tok = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if hf_tok:
        headers["Authorization"] = f"Bearer {hf_tok}"
    if resume_bytes > 0:
        headers["Range"] = f"bytes={resume_bytes}-"
    r = requests.get(url, stream=True, timeout=(10, 60), headers=headers)
    if r.status_code == 416:
        r.close()
        resume_bytes = 0
        headers.pop("Range", None)
        r = requests.get(url, stream=True, timeout=(10, 60), headers=headers)
    r.raise_for_status()
    is_partial = (r.status_code == 206 and resume_bytes > 0)
    if not is_partial:
        resume_bytes = 0
    content_len = int(r.headers.get("content-length", 0) or 0)
    total = (resume_bytes + content_len) if content_len > 0 else 0
    got = resume_bytes
    t0 = time.time()
    last_cb_t = 0.0
    window = [(t0, got)]
    mode = "ab" if is_partial else "wb"
    with open(tmp, mode) as f:
        for blk in r.iter_content(chunk_size=chunk):
            if cancel_check and cancel_check():
                r.close()
                raise RuntimeError("Загрузка отменена пользователем")
            if not blk:
                continue
            f.write(blk)
            got += len(blk)
            now = time.time()
            window.append((now, got))
            while len(window) > 2 and (now - window[0][0]) > 2.0:
                window.pop(0)
            if progress_cb and (now - last_cb_t >= 0.12 or (total and got >= total)):
                dt = max(0.15, now - window[0][0])
                db = max(0, got - window[0][1])
                speed = db / dt if len(window) > 1 else (got - resume_bytes) / max(0.25, now - t0)
                progress_cb(got, total or got, speed)
                last_cb_t = now
    os.replace(tmp, dest)
    return dest
def _extract_archive(archive_path, target_dir):
    base = Path(target_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)
    if archive_path.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as z:
            for info in z.infolist():
                norm = info.filename.replace("\\", "/").lstrip("/")
                if not norm or ".." in norm.split("/") or (":" in norm.split("/")[0]):
                    raise ValueError(f"Unsafe zip entry: {info.filename}")
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise ValueError(f"Symlink entry disallowed in zip: {info.filename}")
                dest = (base / norm).resolve()
                if not dest.is_relative_to(base):
                    raise ValueError(f"Path traversal in zip entry: {info.filename}")
                if info.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info, "r") as src, open(dest, "wb") as dst:
                        shutil.copyfileobj(src, dst)
        return
    if archive_path.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive_path, "r:gz") as t:
            for member in t.getmembers():
                norm = member.name.replace("\\", "/").lstrip("/")
                if not norm or ".." in norm.split("/") or (":" in norm.split("/")[0]):
