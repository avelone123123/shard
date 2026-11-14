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
