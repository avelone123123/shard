import copy
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import threading
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
_WIN = os.name == "nt"
if _WIN:
    import msvcrt
else:
    import fcntl
_GLOBAL_MUTEX = threading.Lock()
_PATH_RLOCKS = {}
_THREAD_DEPTHS = {}
_OPEN_LOCK_FDS = {}
_CREATE_NO_WINDOW = 0x08000000 if _WIN else 0
def _norm_key(path):
    return os.path.normcase(os.path.abspath(str(path)))
def _get_rlock(key):
    with _GLOBAL_MUTEX:
        lk = _PATH_RLOCKS.get(key)
        if lk is None:
            lk = threading.RLock()
            _PATH_RLOCKS[key] = lk
        return lk
def _acquire_os_lock(lock_file, timeout):
    os.makedirs(os.path.dirname(os.path.abspath(lock_file)), exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR
    fd = os.open(lock_file, flags, 0o600)
    deadline = time.monotonic() + max(0.5, float(timeout))
    try:
        while True:
            try:
                if _WIN:
                    if os.fstat(fd).st_size == 0:
                        os.write(fd, b"\0")
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for lock {lock_file}")
                time.sleep(0.015)
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        raise
def _release_os_lock(fd):
    if fd is None:
        return
    try:
        if _WIN:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        os.close(fd)
    except Exception:
        pass
@contextmanager
def file_lock(target_path, timeout=15.0):
    target_abs = os.path.abspath(str(target_path))
    key = _norm_key(target_abs)
    lock_file = target_abs + ".lock"
    rlock = _get_rlock(key)
    rlock.acquire()
    tid = threading.get_ident()
    dkey = (tid, key)
    fd = None
    try:
        with _GLOBAL_MUTEX:
            depth = _THREAD_DEPTHS.get(dkey, 0)
            _THREAD_DEPTHS[dkey] = depth + 1
        if depth == 0:
            fd = _acquire_os_lock(lock_file, timeout)
            with _GLOBAL_MUTEX:
                _OPEN_LOCK_FDS[dkey] = fd
        yield
    finally:
        with _GLOBAL_MUTEX:
            depth = _THREAD_DEPTHS.get(dkey, 1) - 1
            if depth <= 0:
                _THREAD_DEPTHS.pop(dkey, None)
                fd = _OPEN_LOCK_FDS.pop(dkey, fd)
            else:
                _THREAD_DEPTHS[dkey] = depth
                fd = None
        if fd is not None:
            _release_os_lock(fd)
        rlock.release()
def restrict_file_permissions(path):
    p = str(path)
    if not os.path.exists(p):
        return
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass
    if _WIN:
        user = os.environ.get("USERNAME") or ""
        if user:
            try:
                subprocess.run(
                    ["icacls", p, "/inheritance:r", "/grant:r", f"{user}:(R,W)"],
                    capture_output=True,
                    timeout=5,
                    creationflags=_CREATE_NO_WINDOW,
                )
            except Exception:
                pass
def atomic_write_bytes(path, data_bytes, fsync=True, backup=False, restrict=False):
    target = os.path.abspath(str(path))
    parent = os.path.dirname(target)
    os.makedirs(parent, exist_ok=True)
    with file_lock(target):
        if backup and os.path.isfile(target) and os.path.getsize(target) > 0:
            bak_tmp = f"{target}.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(3)}.bak.tmp"
            try:
                shutil.copyfile(target, bak_tmp)
                if fsync:
                    with open(bak_tmp, "rb+") as bf:
                        os.fsync(bf.fileno())
                os.replace(bak_tmp, target + ".bak")
                if restrict:
                    restrict_file_permissions(target + ".bak")
            except Exception:
                try:
                    if os.path.exists(bak_tmp):
                        os.remove(bak_tmp)
                except Exception:
                    pass
        tmp = f"{target}.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(4)}.tmp"
        try:
            if not _WIN and restrict:
                fd_tmp = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd_tmp, "wb") as f:
                    f.write(data_bytes)
                    f.flush()
                    if fsync:
                        os.fsync(f.fileno())
            else:
                with open(tmp, "wb") as f:
                    f.write(data_bytes)
                    f.flush()
                    if fsync:
                        os.fsync(f.fileno())
            os.replace(tmp, target)
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            raise
        if restrict:
            restrict_file_permissions(target)
def atomic_write_text(path, text, fsync=True, backup=False, restrict=False):
    atomic_write_bytes(
        path,
        str(text).encode("utf-8"),
        fsync=fsync,
        backup=backup,
        restrict=restrict,
    )
def atomic_write_json(path, data, indent=2, fsync=True, backup=True, restrict=False):
    payload = json.dumps(data, ensure_ascii=False, indent=indent)
    atomic_write_text(path, payload, fsync=fsync, backup=backup, restrict=restrict)
def _quarantine_file(path):
    p = os.path.abspath(str(path))
    if not os.path.isfile(p):
        return None
    try:
        if os.path.getsize(p) == 0:
            return None
        dest = f"{p}.corrupt.{int(time.time())}"
        if os.path.exists(dest):
            dest = f"{dest}.{secrets.token_hex(2)}"
        shutil.copy2(p, dest)
        return dest
    except Exception:
        return None
def read_json_safe(path, default=None, expected_type=None, quarantine=True, use_backup=True):
    target = os.path.abspath(str(path))
    with file_lock(target):
        if not os.path.exists(target):
            return copy.deepcopy(default)
        if os.path.isfile(target):
            try:
                with open(target, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if expected_type is None or isinstance(data, expected_type):
                    return data
            except Exception:
                pass
            if quarantine:
                _quarantine_file(target)
        bak = target + ".bak"
        if use_backup and os.path.isfile(bak):
            try:
                with open(bak, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if expected_type is None or isinstance(data, expected_type):
                    try:
                        atomic_write_bytes(
                            target,
                            json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"),
                            fsync=True,
                            backup=False,
                        )
                    except Exception:
                        pass
                    return data
            except Exception:
                pass
        return copy.deepcopy(default)
def _quarantine_bad_lines(path, bad_lines):
    if not bad_lines:
        return None
    raw = "\n".join(bad_lines) + "\n"
    h = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:8]
    dest = f"{os.path.abspath(str(path))}.corrupt.{h}"
    if not os.path.exists(dest):
        try:
            with open(dest, "w", encoding="utf-8") as f:
                f.write(raw)
        except Exception:
            return None
    return dest
def read_jsonl_safe(path, quarantine=True):
    target = os.path.abspath(str(path))
    if not os.path.isfile(target):
        return [], 0, None
    with file_lock(target):
        rows = []
        bad = []
        try:
            with open(target, "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s:
                        continue
                    try:
                        obj = json.loads(s)
                        if isinstance(obj, dict):
                            rows.append(obj)
                        else:
                            bad.append(s)
                    except Exception:
                        bad.append(s)
        except Exception:
            if quarantine:
                q = _quarantine_file(target)
                return [], 1, q
            return [], 1, None
        qpath = _quarantine_bad_lines(target, bad) if (bad and quarantine) else None
        return rows, len(bad), qpath
def append_jsonl_locked(path, record, fsync=True):
    target = os.path.abspath(str(path))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    line_bytes = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    with file_lock(target):
        need_nl = False
        if os.path.isfile(target) and os.path.getsize(target) > 0:
            try:
                with open(target, "rb") as rf:
                    rf.seek(-1, os.SEEK_END)
                    need_nl = rf.read(1) != b"\n"
            except Exception:
                need_nl = True
        with open(target, "ab") as f:
            if need_nl:
                f.write(b"\n")
            f.write(line_bytes)
            f.flush()
            if fsync:
                os.fsync(f.fileno())
def sha256_file(path):
    h = hashlib.sha256()
    with open(str(path), "rb") as f:
        for blk in iter(lambda: f.read(65536), b""):
            h.update(blk)
    return h.hexdigest()
def normalize_rel_path(target_path, base_dir):
    if not target_path or not base_dir:
        return None
    try:
        t = Path(target_path).resolve()
        b = Path(base_dir).resolve()
        return t.relative_to(b).as_posix()
    except Exception:
        return None
def resolve_portable_path(abs_path=None, rel_path=None, base_dir=None):
    cand_abs = str(abs_path or "").strip()
    if cand_abs and os.path.exists(cand_abs):
        return cand_abs
    if rel_path and base_dir:
        try:
            b = Path(base_dir).resolve()
            cand_rel = (b / str(rel_path)).resolve()
            if cand_rel.is_relative_to(b) and cand_rel.exists():
                return str(cand_rel)
        except Exception:
            pass
    return cand_abs
def _collect_state_files(data_dir, config_dir, include_runs=True, include_datasets=True):
    droot = Path(data_dir).resolve()
    croot = Path(config_dir).resolve()
    items = []
    skip_dirs = {"models", "cache", "logs", "run", "runtime", "backups", "__pycache__"}
    if not include_runs:
        skip_dirs.add("runs")
    if not include_datasets:
        skip_dirs.add("datasets")
    def _walk_scope(root, scope):
        if not root.is_dir():
            return
        for cur_root, dirs, files in os.walk(str(root)):
            cur = Path(cur_root)
            if cur == root:
                dirs[:] = [d for d in dirs if d not in skip_dirs]
            elif cur.parent == root and cur.name == "training":
                dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fn in files:
                if fn.endswith((".lock", ".tmp", ".bak")) or ".corrupt." in fn or ".tmp." in fn:
                    continue
                fp = cur / fn
                if scope == "data":
                    if fp.parent == root and fp.name not in ("lan.json", "active_model.json", "keys_db.json", "settings.json", "owner_token.txt", "network.json", "network_invites.json", "network_peers.json", "node_identity.json"):
                        continue
                    if fp.parent != root and "training" not in fp.relative_to(root).parts[:1]:
                        continue
                rel = fp.relative_to(root).as_posix()
                items.append((f"{scope}/{rel}", fp))
    _walk_scope(droot, "data")
    if croot != droot:
        for fn in ("keys_db.json", "settings.json", "owner_token.txt", "network.json", "network_invites.json", "node_identity.json"):
            fp = croot / fn
            if fp.is_file():
                items.append((f"config/{fn}", fp))
    return items
def create_state_backup(data_dir, config_dir, output_path=None, include_runs=True, include_datasets=True):
    from . import __version__
    droot = Path(data_dir).resolve()
    backups_dir = droot / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    dest = Path(output_path).resolve() if output_path else (backups_dir / f"shard-backup-{ts}.zip")
    dest.parent.mkdir(parents=True, exist_ok=True)
    items = _collect_state_files(droot, config_dir, include_runs=include_runs, include_datasets=include_datasets)
    manifest_files = {}
    tmp_zip = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    try:
        with zipfile.ZipFile(str(tmp_zip), "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for arc_name, fp in items:
                with file_lock(str(fp)):
                    raw = fp.read_bytes()
                manifest_files[arc_name] = {
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size": len(raw),
                }
                zf.writestr(arc_name, raw)
            manifest = {
                "format": "shard-backup-v1",
                "shard_version": __version__,
                "created_at": time.time(),
                "source_data_dir": str(droot),
                "source_config_dir": str(Path(config_dir).resolve()),
                "file_count": len(manifest_files),
                "files": manifest_files,
            }
            zf.writestr("backup_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        os.replace(str(tmp_zip), str(dest))
        restrict_file_permissions(str(dest))
    except Exception:
        try:
            if tmp_zip.exists():
                tmp_zip.unlink()
        except Exception:
            pass
        raise
    return {
        "status": "ok",
        "backup_path": str(dest),
        "file_count": len(manifest_files),
        "size_bytes": dest.stat().st_size,
        "created_at": manifest["created_at"],
    }
def _safe_arc_target(arc_name, data_dir, config_dir):
    norm = arc_name.replace("\\", "/").lstrip("/")
    if not norm or ".." in norm.split("/"):
        raise ValueError(f"Unsafe archive entry path: {arc_name}")
    parts = norm.split("/")
    if len(parts) < 2:
        raise ValueError(f"Unexpected archive entry: {arc_name}")
    scope, sub_parts = parts[0], parts[1:]
    base = Path(data_dir).resolve() if scope == "data" else (Path(config_dir).resolve() if scope == "config" else None)
    if base is None:
        raise ValueError(f"Unsupported archive scope: {scope}")
    target = (base / Path(*sub_parts)).resolve()
    if not target.is_relative_to(base):
        raise ValueError(f"Path traversal in archive entry: {arc_name}")
    return target
def restore_state_backup(archive_path, data_dir, config_dir, verify_hashes=True):
    src = Path(archive_path).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Backup archive not found: {src}")
    droot = Path(data_dir).resolve()
    croot = Path(config_dir).resolve()
    with zipfile.ZipFile(str(src), "r") as zf:
        names = zf.namelist()
        if "backup_manifest.json" not in names:
            raise ValueError("Invalid SHARD backup archive: missing backup_manifest.json")
        manifest = json.loads(zf.read("backup_manifest.json").decode("utf-8"))
        if manifest.get("format") != "shard-backup-v1":
            raise ValueError("Unsupported SHARD backup format")
        files_meta = manifest.get("files") or {}
        staged = []
        for arc_name, meta in files_meta.items():
            if arc_name not in names:
                raise ValueError(f"Missing file in archive: {arc_name}")
            target = _safe_arc_target(arc_name, droot, croot)
            raw = zf.read(arc_name)
            if verify_hashes:
                digest = hashlib.sha256(raw).hexdigest()
                if digest != meta.get("sha256"):
                    raise ValueError(f"SHA-256 checksum mismatch for {arc_name}")
            staged.append((arc_name, target, raw))
    pre_snap = None
    try:
        pre_out = droot / "backups" / f"pre-restore-{int(time.time())}.zip"
        pre_snap = create_state_backup(droot, croot, output_path=pre_out).get("backup_path")
    except Exception:
        pre_snap = None
    restored = 0
    for _, target, raw in staged:
        restrict = target.name in ("keys_db.json", "owner_token.txt", ".privacy_salt", "node_identity.json", "network_invites.json")
        atomic_write_bytes(target, raw, fsync=True, backup=True, restrict=restrict)
        restored += 1
    return {
        "status": "ok",
        "restored_files": restored,
        "backup_path": str(src),
        "pre_restore_backup": pre_snap,
    }
