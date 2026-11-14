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
        if method == "pypi":
            if (parent_dir / ".git").is_dir() and (parent_dir / "pyproject.toml").is_file():
                method = "git_clone"
                editable = True
            elif (parent_dir / "pyproject.toml").is_file():
                method = "editable_source"
                editable = True
    git_bin = shutil.which("git")
    git_dirty = False
    git_branch = None
    git_commit = None
    repo_root = str(parent_dir) if method in ("git_clone", "editable_source") else None
    if probe_git and method == "git_clone" and git_bin and repo_root:
        try:
            st = subprocess.run([git_bin, "status", "--porcelain", "--untracked-files=no"], cwd=repo_root, capture_output=True, text=True, timeout=5, creationflags=_WIN_NO_WINDOW)
            git_dirty = bool((st.stdout or "").strip())
            br = subprocess.run([git_bin, "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root, capture_output=True, text=True, timeout=5, creationflags=_WIN_NO_WINDOW)
            if br.returncode == 0:
                git_branch = br.stdout.strip()
            cm = subprocess.run([git_bin, "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, timeout=5, creationflags=_WIN_NO_WINDOW)
            if cm.returncode == 0:
                git_commit = cm.stdout.strip()
        except Exception:
            pass
    writable = os.access(str(pkg_dir), os.W_OK)
    return {
        "method": method,
        "editable": editable,
        "package_dir": str(pkg_dir),
        "repo_root": repo_root,
        "python_executable": sys.executable,
        "in_virtualenv": sys.prefix != getattr(sys, "base_prefix", sys.prefix),
        "writable": writable,
        "git_available": bool(git_bin),
        "git_dirty": git_dirty,
        "git_branch": git_branch,
        "git_commit": git_commit,
        "direct_url_info": direct_info,
    }
def _http_get(url, timeout=3.0, headers=None, stream=False):
    import requests
    validate_remote_url(url)
    hdrs = {"User-Agent": f"SHARD-Updater/{__version__}"}
    if headers:
        hdrs.update(headers)
    resp = requests.get(url, headers=hdrs, timeout=timeout, verify=True, allow_redirects=True, stream=stream)
    final_url = getattr(resp, "url", None)
    if isinstance(final_url, str) and final_url:
        validate_remote_url(final_url)
    return resp
def _parse_sha256_text(text, asset_name):
    for line in str(text or "").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            fn = os.path.basename(parts[1].lstrip("*"))
            if fn == asset_name:
                return parts[0].lower()
    return None
def fetch_latest_remote_info(timeout=3.0, allow_prerelease=False):
    headers = {"Accept": "application/vnd.github+json"}
    rate_limited = False
    last_err = None
    try:
        r = _http_get(GITHUB_API_RELEASES_LATEST, timeout=timeout, headers=headers)
        if r.status_code == 200:
            data = r.json() or {}
            tag = str(data.get("tag_name") or "").strip()
            ver = tag.lstrip("vV")
            if is_valid_version_str(ver) and (allow_prerelease or (not data.get("prerelease") and not is_prerelease_version(ver))):
                assets = data.get("assets") or []
                whl_asset = None
                checksum_asset = None
                for a in assets:
                    aname = str(a.get("name") or "")
                    if aname.endswith(".whl") and whl_asset is None:
                        whl_asset = a
                    if aname.lower() in ("sha256sums", "sha256sums.txt", f"{aname}.sha256") or aname.lower().endswith(".sha256"):
                        checksum_asset = a
                asset_url = None
                asset_size = None
                asset_sha256 = None
                if whl_asset:
                    asset_url = whl_asset.get("browser_download_url")
                    asset_size = whl_asset.get("size")
                    dig = str(whl_asset.get("digest") or "")
                    if dig.startswith("sha256:"):
                        asset_sha256 = dig.split(":", 1)[1].strip().lower()
                    elif checksum_asset and checksum_asset.get("browser_download_url"):
                        try:
                            cr = _http_get(checksum_asset["browser_download_url"], timeout=timeout)
                            if cr.status_code == 200:
                                asset_sha256 = _parse_sha256_text(cr.text, whl_asset.get("name", ""))
                        except Exception:
                            pass
                if not asset_url:
                    asset_url = data.get("zipball_url") or f"https://github.com/{GITHUB_REPO}/archive/refs/tags/{tag}.zip"
                return {
                    "ok": True,
                    "source": "github_release",
                    "latest_version": ver,
                    "tag_name": tag,
                    "release_url": data.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases/tag/{tag}",
                    "release_notes": (data.get("body") or "").strip(),
                    "published_at": data.get("published_at"),
                    "asset_url": asset_url,
                    "asset_name": whl_asset.get("name") if whl_asset else f"shard-{ver}.zip",
                    "asset_size": asset_size,
                    "asset_sha256": asset_sha256,
                    "prerelease": bool(data.get("prerelease")),
                }
        elif r.status_code in (403, 429):
            rate_limited = True
            last_err = f"GitHub API rate limit (HTTP {r.status_code})"
        elif r.status_code != 404:
            last_err = f"GitHub Releases HTTP {r.status_code}"
    except Exception as e:
        last_err = str(e)
    if not rate_limited:
        try:
            rt = _http_get(GITHUB_API_TAGS, timeout=timeout, headers=headers)
            if rt.status_code == 200:
                best_ver = None
                best_tag = None
                rt_list = rt.json()
                for item in (rt_list if isinstance(rt_list, list) else []):
                    if not isinstance(item, dict):
                        continue
                    t = str(item.get("name") or "").strip()
                    v = t.lstrip("vV")
                    if not is_valid_version_str(v):
                        continue
                    if not allow_prerelease and is_prerelease_version(v):
                        continue
                    if best_ver is None or parse_version_tuple(v) > parse_version_tuple(best_ver):
                        best_ver = v
                        best_tag = t
                if best_ver:
                    return {
                        "ok": True,
                        "source": "github_tags",
                        "latest_version": best_ver,
                        "tag_name": best_tag,
                        "release_url": f"https://github.com/{GITHUB_REPO}/releases/tag/{best_tag}",
                        "release_notes": "",
