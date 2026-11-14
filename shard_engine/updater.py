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
                        "published_at": None,
                        "asset_url": f"https://github.com/{GITHUB_REPO}/archive/refs/tags/{best_tag}.zip",
                        "asset_name": f"{best_tag}.zip",
                        "asset_size": None,
                        "asset_sha256": None,
                        "prerelease": False,
                    }
            elif rt.status_code in (403, 429):
                rate_limited = True
                last_err = f"GitHub Tags rate limit (HTTP {rt.status_code})"
        except Exception as e:
            last_err = str(e)
    try:
        rp = _http_get(PYPI_JSON_URL, timeout=timeout)
        if rp.status_code == 200:
            pdata = rp.json() or {}
            ver = str((pdata.get("info") or {}).get("version") or "").strip()
            if is_valid_version_str(ver) and (allow_prerelease or not is_prerelease_version(ver)):
                urls = pdata.get("urls") or []
                whl = next((u for u in urls if str(u.get("packagetype")) == "bdist_wheel"), None) or (urls[0] if urls else {})
                return {
                    "ok": True,
                    "source": "pypi",
                    "latest_version": ver,
                    "tag_name": f"v{ver}",
                    "release_url": f"https://pypi.org/project/{__app_name__}/{ver}/",
                    "release_notes": "",
                    "published_at": whl.get("upload_time_iso_8601"),
                    "asset_url": whl.get("url"),
                    "asset_name": whl.get("filename"),
                    "asset_size": whl.get("size"),
                    "asset_sha256": (whl.get("digests") or {}).get("sha256"),
                    "prerelease": False,
                }
    except Exception as e:
        last_err = last_err or str(e)
    try:
        rr = _http_get(GITHUB_RAW_INIT, timeout=timeout)
        if rr.status_code == 200:
            m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', rr.text)
            if m and is_valid_version_str(m.group(1)):
                ver = m.group(1).strip()
                if allow_prerelease or not is_prerelease_version(ver):
                    return {
                        "ok": True,
                        "source": "github_main",
                        "latest_version": ver,
                        "tag_name": f"v{ver}",
                        "release_url": f"https://github.com/{GITHUB_REPO}",
                        "release_notes": "",
                        "published_at": None,
                        "asset_url": f"https://github.com/{GITHUB_REPO}/archive/refs/heads/main.zip",
                        "asset_name": "shard-main.zip",
                        "asset_size": None,
                        "asset_sha256": None,
                        "prerelease": False,
                    }
    except Exception as e:
        last_err = last_err or str(e)
    return {
        "ok": False,
        "rate_limited": rate_limited,
        "error": last_err or "GitHub and PyPI are unreachable.",
    }
def check_for_updates(force=False, timeout=2.5, allow_network=True, allow_prerelease=False, probe_git=False):
    inst = detect_installation_method(probe_git=probe_git)
    cur_ver = __version__
    if not is_valid_version_str(cur_ver):
        return {
            "state": "VERSION_UNKNOWN",
            "current_version": cur_ver,
            "latest_version": None,
            "update_available": False,
            "cached": False,
            "install_method": inst,
            "error": "Installed SHARD version could not be determined.",
            "checked_at": time.time(),
        }
    cached = _read_cache()
    if not force and cached and cached.get("current_version") == cur_ver:
        age = time.time() - float(cached.get("checked_at") or 0)
        if age < CACHE_TTL_SECONDS and cached.get("state") in ("UP_TO_DATE", "UPDATE_AVAILABLE"):
            out = dict(cached)
            out["cached"] = True
            out["install_method"] = inst
            return out
    if not allow_network:
        last_lat = cached.get("latest_version") if isinstance(cached, dict) else None
        return {
            "state": "GITHUB_UNAVAILABLE",
            "current_version": cur_ver,
            "latest_version": last_lat,
            "update_available": bool(last_lat and is_valid_version_str(last_lat) and compare_versions(cur_ver, last_lat) > 0),
            "cached": bool(cached),
            "install_method": inst,
            "error": "Network update check skipped.",
            "checked_at": cached.get("checked_at") if isinstance(cached, dict) else None,
        }
    try:
        remote = fetch_latest_remote_info(timeout=timeout, allow_prerelease=allow_prerelease)
    except Exception as e:
        remote = {"ok": False, "error": str(e), "rate_limited": False}
    if not remote.get("ok"):
        err_msg = remote.get("error") or "GitHub unavailable"
        is_net_err = any(k in err_msg.lower() for k in ("timed out", "timeout", "connection", "name", "dns", "ssl", "rate limit", "unreachable", "http 403", "http 429", "http 5"))
        st_code = "GITHUB_UNAVAILABLE" if (remote.get("rate_limited") or is_net_err or "http " not in err_msg.lower()) else "CHECK_FAILED"
        last_lat = cached.get("latest_version") if isinstance(cached, dict) else None
        payload = {
            "schema_version": 1,
            "state": st_code,
            "current_version": cur_ver,
            "latest_version": last_lat,
            "update_available": bool(last_lat and is_valid_version_str(last_lat) and compare_versions(cur_ver, last_lat) > 0),
            "source": cached.get("source") if isinstance(cached, dict) else None,
            "release_url": cached.get("release_url") if isinstance(cached, dict) else f"https://github.com/{GITHUB_REPO}/releases",
            "release_notes": cached.get("release_notes") if isinstance(cached, dict) else "",
            "published_at": cached.get("published_at") if isinstance(cached, dict) else None,
            "asset_url": cached.get("asset_url") if isinstance(cached, dict) else None,
            "asset_name": cached.get("asset_name") if isinstance(cached, dict) else None,
            "asset_size": cached.get("asset_size") if isinstance(cached, dict) else None,
            "asset_sha256": cached.get("asset_sha256") if isinstance(cached, dict) else None,
            "cached": False,
            "install_method": inst,
            "error": err_msg,
            "checked_at": time.time(),
        }
        return payload
    latest_ver = remote["latest_version"]
    cmp_res = compare_versions(cur_ver, latest_ver)
    state = "UPDATE_AVAILABLE" if cmp_res > 0 else "UP_TO_DATE"
    payload = {
        "schema_version": 1,
        "state": state,
        "current_version": cur_ver,
        "latest_version": latest_ver,
        "tag_name": remote.get("tag_name"),
        "update_available": cmp_res > 0,
        "source": remote.get("source"),
        "release_url": remote.get("release_url"),
        "release_notes": remote.get("release_notes") or "",
        "published_at": remote.get("published_at"),
        "asset_url": remote.get("asset_url"),
        "asset_name": remote.get("asset_name"),
        "asset_size": remote.get("asset_size"),
        "asset_sha256": remote.get("asset_sha256"),
        "cached": False,
        "install_method": inst,
        "error": None,
        "checked_at": time.time(),
    }
    _write_cache(payload)
    return payload
def check_for_updates_async(force=False, timeout=2.0):
    def _worker():
        try:
            check_for_updates(force=force, timeout=timeout, allow_network=True)
        except Exception:
            pass
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t
def startup_check_banner():
    try:
        res = check_for_updates(force=False, timeout=1.5, allow_network=True)
        st = res.get("state")
        if st == "UPDATE_AVAILABLE":
            return f"SHARD {res['current_version']} | Latest version: {res['latest_version']} | Update available! Run: shard update"
        if st == "UP_TO_DATE":
            return f"SHARD {res['current_version']} (up to date)"
        if st == "GITHUB_UNAVAILABLE":
            return f"SHARD {res['current_version']} (offline mode: GitHub update check unavailable)"
        return None
    except Exception:
        return None
def verify_archive_artifact(archive_path, expected_sha256=None, expect_wheel=False):
    ap = Path(archive_path)
    if not ap.is_file() or ap.stat().st_size == 0:
        raise ValueError("Downloaded update archive is empty or missing.")
    actual_sha = storage.sha256_file(ap).lower()
    if expected_sha256:
        if actual_sha != str(expected_sha256).strip().lower():
            raise ValueError(f"Checksum mismatch for downloaded update (expected {expected_sha256[:16]}..., got {actual_sha[:16]}...).")
    if not zipfile.is_zipfile(str(ap)):
        raise ValueError("Downloaded update file is not a valid zip/wheel archive.")
    has_pkg_init = False
    has_wheel_meta = False
    with zipfile.ZipFile(str(ap), "r") as zf:
        bad = zf.testzip()
        if bad is not None:
            raise ValueError(f"Corrupted file inside update archive: {bad}")
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if not name or name.startswith("/") or ".." in name.split("/") or (len(name) > 1 and name[1] == ":"):
                raise ValueError(f"Unsafe path entry inside update archive: {name}")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise ValueError(f"Symlink entry inside update archive rejected: {name}")
            norm = name.strip("/")
            if norm == "shard_engine/__init__.py" or norm.endswith("/shard_engine/__init__.py"):
                has_pkg_init = True
            if norm.endswith(".dist-info/WHEEL") or norm.endswith(".dist-info/METADATA"):
                has_wheel_meta = True
    if not has_pkg_init:
        raise ValueError("Update archive does not contain shard_engine/__init__.py.")
    if expect_wheel and not has_wheel_meta:
        raise ValueError("Wheel archive is missing dist-info metadata.")
    return {"valid": True, "sha256": actual_sha, "size_bytes": ap.stat().st_size}
def create_update_backup(install_info=None):
    info = install_info or detect_installation_method()
    ts = int(time.time())
    bdir = Path(paths.backups_dir())
    bdir.mkdir(parents=True, exist_ok=True)
    state_res = storage.create_state_backup(
        paths.data_dir(),
        paths.config_dir(),
        output_path=bdir / f"pre-update-state-{ts}.zip",
        include_runs=False,
        include_datasets=False,
    )
    pkg_dir = Path(info["package_dir"]).resolve()
    pkg_zip = bdir / f"pre-update-pkg-{__version__}-{ts}.zip"
    tmp_zip = pkg_zip.with_name(f".{pkg_zip.name}.{os.getpid()}.tmp")
    manifest_files = {}
    with zipfile.ZipFile(str(tmp_zip), "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(str(pkg_dir)):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fn in files:
                if fn.endswith((".pyc", ".pyo")):
                    continue
                fp = Path(root) / fn
                rel = fp.relative_to(pkg_dir).as_posix()
                raw = fp.read_bytes()
                manifest_files[rel] = hashlib.sha256(raw).hexdigest()
                zf.writestr(f"shard_engine/{rel}", raw)
        meta = {
            "version": __version__,
            "created_at": time.time(),
            "method": info.get("method"),
            "package_dir": str(pkg_dir),
            "repo_root": info.get("repo_root"),
            "git_commit": info.get("git_commit"),
            "state_backup_path": state_res.get("backup_path"),
            "file_count": len(manifest_files),
            "files": manifest_files,
        }
        zf.writestr("update_pkg_manifest.json", json.dumps(meta, ensure_ascii=False, indent=2))
    os.replace(str(tmp_zip), str(pkg_zip))
    storage.restrict_file_permissions(pkg_zip)
    ptr_path = Path(paths.data_dir()) / "last_update_backup.json"
    ptr = {
        "created_at": time.time(),
        "version": __version__,
        "pkg_backup_path": str(pkg_zip),
        "state_backup_path": state_res.get("backup_path"),
        "method": info.get("method"),
        "package_dir": str(pkg_dir),
        "repo_root": info.get("repo_root"),
        "git_commit": info.get("git_commit"),
    }
    storage.atomic_write_json(ptr_path, ptr, fsync=True, backup=True)
    return ptr
def rollback_update(backup_info=None):
    ptr = backup_info
    if ptr is None:
        ptr_path = Path(paths.data_dir()) / "last_update_backup.json"
        ptr = storage.read_json_safe(ptr_path, default=None, expected_type=dict, quarantine=False, use_backup=True)
    if not isinstance(ptr, dict) or not ptr.get("pkg_backup_path"):
        raise FileNotFoundError("No pre-update backup found to roll back to.")
    pkg_zip = Path(ptr["pkg_backup_path"])
    if not pkg_zip.is_file():
        raise FileNotFoundError(f"Package backup archive missing: {pkg_zip}")
    pkg_dir = Path(ptr.get("package_dir") or Path(__file__).resolve().parent).resolve()
    restored = 0
    with zipfile.ZipFile(str(pkg_zip), "r") as zf:
        if zf.testzip() is not None:
            raise ValueError("Package backup archive is corrupted.")
        meta = json.loads(zf.read("update_pkg_manifest.json").decode("utf-8"))
        files_meta = meta.get("files") or {}
        for rel, expected_sha in files_meta.items():
            arc = f"shard_engine/{rel}"
            raw = zf.read(arc)
            if hashlib.sha256(raw).hexdigest() != expected_sha:
                raise ValueError(f"Backup checksum mismatch for {rel}")
            target = (pkg_dir / rel).resolve()
            if not target.is_relative_to(pkg_dir):
                raise ValueError(f"Unsafe target in backup: {rel}")
            storage.atomic_write_bytes(target, raw, fsync=True, backup=False)
            restored += 1
    repo_root = ptr.get("repo_root")
    git_commit = ptr.get("git_commit")
    if ptr.get("method") == "git_clone" and repo_root and git_commit and shutil.which("git"):
        try:
            subprocess.run([shutil.which("git"), "reset", "--hard", git_commit], cwd=repo_root, capture_output=True, text=True, timeout=20, creationflags=_WIN_NO_WINDOW)
        except Exception:
            pass
    state_zip = ptr.get("state_backup_path")
    if state_zip and os.path.isfile(state_zip):
        try:
            storage.restore_state_backup(state_zip, paths.data_dir(), paths.config_dir(), verify_hashes=True)
        except Exception:
            pass
    return {
        "status": "rolled_back",
        "restored_version": ptr.get("version"),
        "restored_files": restored,
    }
def verify_installation(expected_version=None):
    code = (
        "import json, shard_engine, shard_engine.cli, shard_engine.server, shard_engine.paths as p;"
        "ok = p.package_resource_exists('dashboard.html') and p.package_resource_exists('engine_optimizations.json');"
        "print(json.dumps({'version': shard_engine.__version__, 'assets_ok': bool(ok)}))"
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=_WIN_NO_WINDOW,
    )
    if res.returncode != 0 or not res.stdout.strip():
        raise RuntimeError(f"Post-update verification failed: {(res.stderr or res.stdout)[:300]}")
    data = json.loads(res.stdout.strip().splitlines()[-1])
    if not data.get("assets_ok"):
        raise RuntimeError("Post-update verification failed: required dashboard/assets missing.")
    new_ver = data.get("version")
    if expected_version and is_valid_version_str(expected_version):
        if str(new_ver).lstrip("vV") != str(expected_version).lstrip("vV"):
            raise RuntimeError(f"Updated version mismatch: expected {expected_version}, got {new_ver}.")
    return data
def _download_file(url, dest_path, expected_size=None, progress_cb=None, timeout=60):
    r = _http_get(url, timeout=timeout, stream=True)
    if r.status_code in (403, 429):
        raise RuntimeError(f"Download blocked by rate limit (HTTP {r.status_code}). Retry later.")
    if r.status_code != 200:
        raise RuntimeError(f"Download failed with HTTP {r.status_code}.")
    hdr_len = r.headers.get("Content-Length")
    total_bytes = int(hdr_len) if hdr_len and str(hdr_len).isdigit() else (int(expected_size) if expected_size else 0)
    downloaded = 0
    with open(dest_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=65536):
            if not chunk:
                continue
            f.write(chunk)
            downloaded += len(chunk)
            if progress_cb and total_bytes > 0:
                pct = min(100, int(downloaded * 100 / total_bytes))
                progress_cb(pct, downloaded, total_bytes)
    if total_bytes > 0 and downloaded != total_bytes:
        raise RuntimeError(f"Interrupted download: received {downloaded} bytes of expected {total_bytes} bytes.")
    return downloaded
def _sync_source_archive(zip_path, target_pkg_dir):
    synced = 0
    with zipfile.ZipFile(str(zip_path), "r") as zf:
        pkg_members = []
        for info in zf.infolist():
            norm = info.filename.replace("\\", "/").strip("/")
            parts = norm.split("/")
            if "shard_engine" in parts:
                idx = parts.index("shard_engine")
                rel_parts = parts[idx + 1:]
