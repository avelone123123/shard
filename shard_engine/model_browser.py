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
