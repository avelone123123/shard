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
