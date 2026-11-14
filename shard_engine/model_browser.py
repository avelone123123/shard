import ctypes
import json
import os
import re
from pathlib import Path
from . import gguf_backend, hf_hub, paths, settings
_SENSITIVE_EXACT_NAMES = frozenset({
    ".ssh",
    ".gnupg",
    ".aws",
    ".azure",
    ".kube",
    ".docker",
    ".netrc",
    ".git-credentials",
    ".npmrc",
    ".pypirc",
    "system volume information",
    "$recycle.bin",
    "recovery",
    "owner_token.txt",
    "keys_db.json",
    "node_identity.json",
    "network_invites.json",
    ".privacy_salt",
})
_SENSITIVE_SUBPATHS = (
    ("windows", "system32"),
    ("windows", "syswow64"),
    ("windows", "csc"),
    ("programdata", "microsoft", "crypto"),
    ("appdata", "local", "google"),
    ("appdata", "local", "microsoft", "edge"),
    ("appdata", "local", "bravesoftware"),
    ("appdata", "roaming", "mozilla"),
    ("appdata", "roaming", "opera software"),
    ("appdata", "roaming", "microsoft", "credentials"),
    ("appdata", "local", "microsoft", "credentials"),
    ("appdata", "roaming", "microsoft", "protect"),
    (".config", "google-chrome"),
    (".config", "chromium"),
    (".config", "gcloud"),
    (".mozilla", "firefox"),
    ("library", "application support", "google", "chrome"),
    ("library", "keychains"),
)
_POSIX_BLOCKED_PREFIXES = ("/etc", "/proc", "/sys", "/dev", "/boot", "/root", "/var/run", "/private/etc")
_WEIGHT_EXTS = (".safetensors", ".bin")
_SAFE_HF_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
_DRIVE_LABEL_CACHE = {}
def _volume_label(root_path):
    if os.name != "nt":
        return ""
    norm = os.path.normcase(root_path)
    if norm in _DRIVE_LABEL_CACHE:
        return _DRIVE_LABEL_CACHE[norm]
    label = ""
    try:
        buf = ctypes.create_unicode_buffer(261)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(ctypes.c_wchar_p(root_path), buf, 260, None, None, None, None, 0)
        if ok:
            label = buf.value.strip()
    except Exception:
        label = ""
    _DRIVE_LABEL_CACHE[norm] = label
    return label
def _has_subpath_seq(parts_lower, seq):
    n = len(seq)
    if len(parts_lower) < n:
        return False
    for i in range(len(parts_lower) - n + 1):
        if tuple(parts_lower[i:i + n]) == seq:
            return True
    return False
def is_sensitive_path(path_obj):
    s = str(path_obj)
    raw_posix = s.replace("\\", "/").lower()
    for pref in _POSIX_BLOCKED_PREFIXES:
        if raw_posix == pref or raw_posix.startswith(pref + "/"):
            return True
    try:
        real = os.path.realpath(s)
    except Exception:
        real = s
    for cand in (s, real):
        p = Path(cand)
        parts_lower = [pt.lower() for pt in p.parts if pt not in ("/", "\\")]
        if len(parts_lower) >= 2 and re.match(r"^[a-z]:", parts_lower[0]) and parts_lower[1] == "windows":
            return True
        for pt in parts_lower:
            if pt in _SENSITIVE_EXACT_NAMES:
                return True
        for seq in _SENSITIVE_SUBPATHS:
            if _has_subpath_seq(parts_lower, seq):
                return True
        norm_posix = cand.replace("\\", "/").lower()
        for pref in _POSIX_BLOCKED_PREFIXES:
            if norm_posix == pref or norm_posix.startswith(pref + "/"):
                return True
    return False
def validate_browse_path(raw_path):
    raw = str(raw_path or "").strip()
    if not raw or "\x00" in raw or any(ord(c) < 32 for c in raw):
        raise ValueError("Invalid path.")
    if raw.startswith("\\\\") or raw.startswith("//"):
        raise PermissionError("Network UNC paths are not permitted.")
    expanded = os.path.expanduser(raw)
    resolved = os.path.realpath(os.path.abspath(expanded))
    if resolved.startswith("\\\\") or resolved.startswith("//"):
        raise PermissionError("Network UNC paths are not permitted.")
    if os.name == "nt":
        drv, _ = os.path.splitdrive(resolved)
        if not drv or not re.match(r"^[A-Za-z]:$", drv):
            raise ValueError("Path must be anchored to a valid local drive.")
    if is_sensitive_path(expanded) or is_sensitive_path(resolved):
        raise PermissionError("Access to system or sensitive credential directory is blocked.")
    return Path(resolved)
def _fmt_size(size_bytes):
    if size_bytes is None or size_bytes < 0:
        return "0 МБ"
    mb = size_bytes / (1024 * 1024)
    if mb >= 1024:
        return f"{round(mb / 1024, 2)} ГБ"
    return f"{round(mb, 1)} МБ"
def _bounded_dir_size(dir_path, max_files=250):
    total = 0
    count = 0
    try:
        for root, dirs, files in os.walk(str(dir_path)):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
            for fn in files:
                fp = os.path.join(root, fn)
                try:
                    total += os.path.getsize(fp)
                except Exception:
                    pass
                count += 1
                if count >= max_files:
                    return total, True
    except Exception:
        pass
    return total, False
def _read_json_small(path_obj):
    try:
        if path_obj.is_file() and path_obj.stat().st_size <= 512 * 1024:
            return json.loads(path_obj.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return None
def map_gguf_to_hf_base(path_or_name):
    raw = str(path_or_name or "").strip()
    if not raw:
        return {"suggested_hf_base_repo": None, "hf_base_cached_locally": False, "local_hf_snapshot_path": None}
    norm = raw.replace("\\", "/")
    m_cache = re.search(r"models--([^/]+)--([^/]+)", norm)
    cand_repo = None
    if m_cache:
        cand_repo = f"{m_cache.group(1)}/{m_cache.group(2)}"
    base_name = os.path.basename(norm)
    stem = re.sub(r"(?i)\.gguf$", "", base_name)
    stem = re.sub(r"(?i)[-_.]?(?:i1-)?(?:IQ\d_[A-Z0-9_]+|Q\d+_K(?:_[A-Z]+)?|Q\d+_\d+|Q\d+|F16|F32|BF16)(?:-\d+-of-\d+)?$", "", stem)
    stem = re.sub(r"(?i)[-_]?(?:imatrix|gguf)$", "", stem).strip("-_.")
    stem_low = stem.lower()
    suggested = None
    for m in hf_hub.CURATED_MODELS:
        mid = m["id"]
        mtail = mid.split("/")[-1].lower()
        if mtail == stem_low or stem_low in mtail or mtail in stem_low:
            suggested = mid
            break
    if not suggested and cand_repo:
        cleaned_repo = re.sub(r"(?i)[-_]?gguf$", "", cand_repo)
        author = cleaned_repo.split("/")[0] if "/" in cleaned_repo else ""
        tail = cleaned_repo.split("/")[-1]
        org_map = {
            "bartowski": None,
            "unsloth": None,
            "maziyarpanahi": None,
            "thebloke": None,
            "mradermacher": None,
        }
        if author.lower() in org_map:
            for m in hf_hub.CURATED_MODELS:
                if m["id"].split("/")[-1].lower() == tail.lower():
                    suggested = m["id"]
                    break
        elif "/" in cleaned_repo:
            suggested = cleaned_repo
    if not suggested and stem:
        if "qwen" in stem_low:
            suggested = f"Qwen/{stem}"
        elif "llama" in stem_low:
            suggested = f"meta-llama/{stem}"
        elif "deepseek" in stem_low:
            suggested = f"deepseek-ai/{stem}"
        elif "gemma" in stem_low:
            suggested = f"google/{stem}"
        elif "mistral" in stem_low:
            suggested = f"mistralai/{stem}"
        elif "phi" in stem_low:
            suggested = f"microsoft/{stem}"
        elif "smollm" in stem_low:
            suggested = f"HuggingFaceTB/{stem}"
    local_hf = None
    cached_locally = False
    if suggested:
        cache_folder_name = f"models--{suggested.replace('/', '--')}"
        for hub_root in gguf_backend.all_hub_dirs():
            cand_dir = Path(hub_root) / cache_folder_name / "snapshots"
            if cand_dir.is_dir():
                for snap in sorted(cand_dir.iterdir()):
                    if snap.is_dir() and (snap / "config.json").is_file() and any(snap.glob("*.safetensors")):
                        local_hf = str(snap)
                        cached_locally = True
                        break
            if cached_locally:
                break
    return {
        "suggested_hf_base_repo": suggested,
        "hf_base_cached_locally": cached_locally,
        "local_hf_snapshot_path": local_hf,
    }
def _compat_block(gguf_ok, gguf_reason, pt_ok, pt_reason, soup_ok, soup_reason, suggested_base=None, resolved_base=None):
    return {
        "gguf_inference": {"supported": bool(gguf_ok), "reason_ru": gguf_reason},
        "pytorch_inference": {"supported": bool(pt_ok), "reason_ru": pt_reason},
        "soup_training": {
            "supported": bool(soup_ok),
            "can_resolve_base": bool(suggested_base),
            "suggested_base_model": suggested_base,
            "resolved_base_model": resolved_base,
            "reason_ru": soup_reason,
        },
    }
def classify_model_path(target_path, deep_size=False):
    p = validate_browse_path(target_path)
    if p.is_file():
        sz = p.stat().st_size
        ext = p.suffix.lower()
        if ext == ".gguf":
