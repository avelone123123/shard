import hashlib
import json
import os
import secrets
import threading
import time
from typing import Optional, Tuple
from . import paths
from . import storage
_REGISTRY_LOCK = threading.Lock()
def _sessions_file():
    p = paths.data_dir() / "sessions"
    p.mkdir(parents=True, exist_ok=True)
    return p / "participant_sessions.json"
def _load_registry_unlocked():
    sf = _sessions_file()
    if not sf.exists():
        return {"version": 1, "sessions": {}}
    try:
        with open(sf, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "sessions" in data:
            return data
    except Exception:
        pass
    return {"version": 1, "sessions": {}}
def _save_registry_unlocked(data):
    storage.atomic_write_json(_sessions_file(), data, indent=2, fsync=True, backup=True, restrict=True)
def hash_token(token: str) -> str:
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
def issue_session(role: str = "member", name: str = "Participant", auth_method: str = "lan_token", ttl_seconds: float = 604800.0) -> Tuple[str, dict]:
    raw_token = secrets.token_urlsafe(32)
    thash = hash_token(raw_token)
    pid = f"p_{secrets.token_hex(8)}"
    now = time.time()
    record = {
        "participant_id": pid,
        "token_hash": thash,
        "role": role,
        "name": str(name or "Participant")[:48],
        "auth_method": auth_method,
        "created_at": now,
        "expires_at": now + max(300.0, float(ttl_seconds)),
        "revoked": False,
    }
    with _REGISTRY_LOCK, storage.file_lock(_sessions_file()):
        reg = _load_registry_unlocked()
        reg["sessions"][thash] = record
        _save_registry_unlocked(reg)
    return raw_token, record
def validate_session(raw_token: str) -> Tuple[bool, Optional[dict], str]:
    if not raw_token or not isinstance(raw_token, str):
        return False, None, "Missing session token"
    thash = hash_token(raw_token)
    now = time.time()
    with _REGISTRY_LOCK, storage.file_lock(_sessions_file()):
        reg = _load_registry_unlocked()
        record = reg.get("sessions", {}).get(thash)
        if not record:
            return False, None, "Unknown session token"
        if record.get("revoked"):
            return False, None, "Session has been revoked"
        if now > float(record.get("expires_at", 0)):
            return False, None, "Session has expired"
        return True, dict(record), ""
def revoke_session(raw_token: str) -> bool:
    if not raw_token or not isinstance(raw_token, str):
        return False
    thash = hash_token(raw_token)
    with _REGISTRY_LOCK, storage.file_lock(_sessions_file()):
        reg = _load_registry_unlocked()
        if thash in reg.get("sessions", {}):
            reg["sessions"][thash]["revoked"] = True
            _save_registry_unlocked(reg)
            return True
    return False
def revoke_participant_sessions(participant_id: str) -> int:
    count = 0
    with _REGISTRY_LOCK, storage.file_lock(_sessions_file()):
        reg = _load_registry_unlocked()
        for thash, rec in reg.get("sessions", {}).items():
            if rec.get("participant_id") == participant_id and not rec.get("revoked"):
                rec["revoked"] = True
                count += 1
        if count > 0:
            _save_registry_unlocked(reg)
    return count
def cleanup_expired_sessions() -> int:
    purged = 0
    now = time.time()
    with _REGISTRY_LOCK, storage.file_lock(_sessions_file()):
        reg = _load_registry_unlocked()
        kept = {}
        for thash, rec in reg.get("sessions", {}).items():
            if rec.get("revoked") and (now - float(rec.get("created_at", 0)) > 86400 * 3):
                purged += 1
                continue
            if now > float(rec.get("expires_at", 0)) + 86400:
                purged += 1
                continue
            kept[thash] = rec
        if purged > 0:
            reg["sessions"] = kept
            _save_registry_unlocked(reg)
    return purged
