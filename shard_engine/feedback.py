import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import uuid
from pathlib import Path
from . import paths
from . import soup_adapter
from . import storage
_LOCK = threading.RLock()
VALID_RATINGS = ("up", "down", "correction", "ab")
VALID_STATUSES = ("pending", "approved", "rejected", "deleted")
DEFAULT_PRIVACY_CONFIG = {
    "allow_feedback": True,
    "allow_remote_feedback": False,
    "require_explicit_consent": True,
    "require_manual_review": True,
    "redact_secrets": True,
    "redact_pii": True,
    "store_raw_conversations": False,
    "retention_days": 90,
    "max_text_chars": 16000,
    "min_text_chars": 2,
}
_SECRET_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----")),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16,32}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{16,}\b")),
    ("owner_token", re.compile(r"\bshard-owner-[A-Za-z0-9_-]{8,}\b")),
    ("hf_token", re.compile(r"\bhf_[A-Za-z0-9]{10,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-+/=]{16,}")),
)
_PII_PATTERNS = (
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"(?<!\w)(?:\+?\d[\d\-\s()]{7,}\d)(?!\w)")),
)
def _base_dir():
    d = Path(paths.training_dir())
    d.mkdir(parents=True, exist_ok=True)
    return d
def _datasets_dir():
    d = Path(paths.training_datasets_dir())
    d.mkdir(parents=True, exist_ok=True)
    return d
def _privacy_path():
    return _base_dir() / "privacy_config.json"
def _feedback_path():
    return _base_dir() / "raw_feedback.jsonl"
def _optouts_path():
    return _base_dir() / "optouts.json"
def _audit_path():
    return _base_dir() / "training_audit.jsonl"
def _privacy_salt():
    sp = Path(paths.training_salt_file())
    with storage.file_lock(sp):
        if sp.is_file():
            try:
                raw = sp.read_text(encoding="utf-8", errors="replace").strip()
                if len(raw) >= 32:
                    return raw.encode("utf-8")
            except Exception:
                pass
        val = secrets.token_hex(32)
        storage.atomic_write_text(sp, val, fsync=True, backup=True, restrict=True)
        return val.encode("utf-8")
def _append_audit(event_type, details=None):
    rec = {
        "ts": time.time(),
        "event": str(event_type),
        "details": details or {},
    }
    with _LOCK:
        storage.append_jsonl_locked(_audit_path(), rec, fsync=True)
def read_audit_log(limit=100):
    with _LOCK:
        rows, _, _ = storage.read_jsonl_safe(_audit_path(), quarantine=True)
        out = rows[-max(1, int(limit)):]
        out.reverse()
        return out
def get_privacy_config():
    with _LOCK:
        cfg = dict(DEFAULT_PRIVACY_CONFIG)
        raw = storage.read_json_safe(
            _privacy_path(),
            default={},
            expected_type=dict,
            quarantine=True,
            use_backup=True,
        )
        if isinstance(raw, dict):
            for k in DEFAULT_PRIVACY_CONFIG:
                if k in raw:
                    cfg[k] = raw[k]
        cfg["store_raw_conversations"] = False
        return cfg
def update_privacy_config(updates):
    if not isinstance(updates, dict):
        raise ValueError("Privacy config updates must be an object.")
    with _LOCK:
        with storage.file_lock(_privacy_path()):
            cfg = get_privacy_config()
            bool_keys = (
                "allow_feedback",
