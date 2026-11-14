import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse
from . import __app_name__, __version__, paths, process, storage
GITHUB_REPO = os.environ.get("SHARD_GITHUB_REPO", "avelone123123/shard")
GITHUB_API_RELEASES_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_API_TAGS = f"https://api.github.com/repos/{GITHUB_REPO}/tags?per_page=15"
GITHUB_RAW_INIT = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/shard_engine/__init__.py"
PYPI_JSON_URL = f"https://pypi.org/pypi/{__app_name__}/json"
CACHE_TTL_SECONDS = 4 * 3600
MIN_UPDATE_FREE_BYTES = 150 * 1024 * 1024
_WIN_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_ALLOWED_HTTPS_HOSTS = frozenset({
    "api.github.com",
    "github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "codeload.github.com",
    "release-assets.githubusercontent.com",
    "pypi.org",
    "files.pythonhosted.org",
})
_SAFE_REF_RE = re.compile(r"^v?[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9._+-]{0,24})?$|^[0-9a-fA-F]{40}$")
_UPDATE_JOB_LOCK = threading.RLock()
_UPDATE_JOB_STATE = {
    "status": "idle",
    "phase": "idle",
    "progress_pct": 0,
    "message": "",
    "logs": [],
    "old_version": __version__,
    "new_version": None,
    "rolled_back": False,
    "restart_required": False,
    "backup_path": None,
    "error": None,
    "started_at": None,
    "finished_at": None,
}
def is_valid_version_str(ver):
    if not ver:
        return False
    s = str(ver).strip()
    if s.startswith("v") or s.startswith("V"):
        s = s[1:]
    if not s or s == "0.0.0":
        return False
    return bool(re.match(r"^\d+(?:\.\d+)+(?:[-._a-zA-Z0-9]+)?$", s))
def is_prerelease_version(ver):
    s = str(ver or "").strip().lower().lstrip("v")
    if not s:
        return False
    return bool(re.search(r"(?:^|[.-])(a|alpha|b|beta|rc|dev|pre)\d*(?:$|[.-])|\d+(?:a|b|rc|dev)\d+$", s))
def parse_version_tuple(ver):
    s = str(ver or "0").strip()
    if s.startswith("v") or s.startswith("V"):
        s = s[1:]
    core = re.split(r"[-+]", s, maxsplit=1)[0]
    nums = []
    for tok in core.split("."):
        m = re.match(r"^(\d+)", tok)
        nums.append(int(m.group(1)) if m else 0)
    while len(nums) < 3:
        nums.append(0)
    pre_rank = 0 if is_prerelease_version(s) else 1
    return tuple(nums[:4]) + (pre_rank,)
def compare_versions(local_ver, remote_ver):
    lv = parse_version_tuple(local_ver)
    rv = parse_version_tuple(remote_ver)
    if rv > lv:
        return 1
    if rv < lv:
        return -1
    return 0
def validate_remote_url(url):
    raw = str(url or "").strip()
    if not raw or len(raw) > 2048:
        raise ValueError("Invalid remote URL.")
    parsed = urlparse(raw)
    if parsed.scheme.lower() != "https":
        raise ValueError(f"Only HTTPS URLs are permitted for updates (got {parsed.scheme!r}).")
    host = (parsed.hostname or "").lower()
    if host not in _ALLOWED_HTTPS_HOSTS:
        raise ValueError(f"Untrusted update host: {host!r}.")
    return raw
def validate_git_ref(ref):
    raw = str(ref or "").strip()
    if not raw or raw.startswith("-") or not _SAFE_REF_RE.match(raw):
        raise ValueError(f"Unsafe update reference/version: {ref!r}")
    return raw
def _cache_paths():
    primary = Path(paths.update_cache_file())
    out = [primary]
    if not os.environ.get("SHARD_DATA_DIR"):
        leg = Path(paths.legacy_update_cache_file())
        if os.path.normcase(str(leg)) != os.path.normcase(str(primary)):
            out.append(leg)
    return out
def _read_cache():
    for p in _cache_paths():
        if p.is_file():
            data = storage.read_json_safe(p, default=None, expected_type=dict, quarantine=True, use_backup=True)
            if isinstance(data, dict):
                return data
    return None
def _write_cache(payload):
    for p in _cache_paths():
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            storage.atomic_write_json(p, payload, fsync=True, backup=True)
        except Exception:
            pass
def detect_installation_method(probe_git=True):
    pkg_dir = Path(__file__).resolve().parent
    parent_dir = pkg_dir.parent
    method = "pypi"
    direct_info = None
    editable = False
    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        method = "packaged"
    else:
        try:
            dist = importlib.metadata.distribution(__app_name__)
            du_text = dist.read_text("direct_url.json")
            if du_text:
                direct_info = json.loads(du_text)
                if (direct_info.get("dir_info") or {}).get("editable"):
                    editable = True
                    method = "git_clone" if (parent_dir / ".git").exists() else "editable_source"
                elif "archive_info" in direct_info or "vcs_info" in direct_info:
                    method = "wheel"
        except Exception:
            pass
