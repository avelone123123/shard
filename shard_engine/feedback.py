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
                "allow_remote_feedback",
                "require_explicit_consent",
                "require_manual_review",
                "redact_secrets",
                "redact_pii",
            )
            for k in bool_keys:
                if k in updates:
                    cfg[k] = bool(updates[k])
            if "retention_days" in updates:
                days = int(updates["retention_days"])
                if days < 1 or days > 3650:
                    raise ValueError("retention_days must be between 1 and 3650.")
                cfg["retention_days"] = days
            if "max_text_chars" in updates:
                mx = int(updates["max_text_chars"])
                if mx < 200 or mx > 200000:
                    raise ValueError("max_text_chars must be between 200 and 200000.")
                cfg["max_text_chars"] = mx
            if "min_text_chars" in updates:
                mn = int(updates["min_text_chars"])
                if mn < 1 or mn > 500:
                    raise ValueError("min_text_chars must be between 1 and 500.")
                cfg["min_text_chars"] = mn
            cfg["store_raw_conversations"] = False
            storage.atomic_write_json(_privacy_path(), cfg, fsync=True, backup=True)
            _append_audit("privacy_config_updated", {"keys": sorted(updates.keys())})
            return cfg
def pseudonymize_contributor(raw_id):
    txt = str(raw_id or "").strip()
    if not txt:
        txt = "anonymous"
    if txt.startswith("contrib-") and len(txt) == 24:
        return txt
    key = _privacy_salt()
    digest = hmac.new(key, txt.encode("utf-8", errors="replace"), hashlib.sha256).hexdigest()[:16]
    return f"contrib-{digest}"
def _load_optouts():
    data = storage.read_json_safe(
        _optouts_path(),
        default={},
        expected_type=dict,
        quarantine=True,
        use_backup=True,
    )
    return data if isinstance(data, dict) else {}
def _save_optouts(data):
    storage.atomic_write_json(_optouts_path(), data, fsync=True, backup=True)
def is_contributor_opted_out(contributor_id):
    cid = pseudonymize_contributor(contributor_id)
    with _LOCK:
        with storage.file_lock(_optouts_path()):
            return cid in _load_optouts()
def opt_in_contributor(contributor_id):
    cid = pseudonymize_contributor(contributor_id)
    with _LOCK:
        with storage.file_lock(_optouts_path()):
            opts = _load_optouts()
            removed = opts.pop(cid, None) is not None
            if removed:
                _save_optouts(opts)
                _append_audit("contributor_reopted_in", {"contributor_id": cid})
            return {"contributor_id": cid, "opted_out": False}
def sanitize_and_screen(text, cfg, allow_empty=False):
    if text is None:
        if allow_empty:
            return "", [], []
        raise ValueError("Text field is missing.")
    s = str(text)
    if "\x00" in s:
        raise ValueError("Null bytes are not permitted in feedback text.")
    s = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s).strip()
    max_len = int(cfg.get("max_text_chars", 16000))
    if len(s) > max_len:
        raise ValueError(f"Feedback text exceeds maximum length ({max_len} chars).")
    if not s:
        if allow_empty:
            return "", [], []
        raise ValueError("Feedback text must not be empty.")
    redactions = []
    quality_flags = []
    if len(s) < int(cfg.get("min_text_chars", 2)):
        quality_flags.append("too_short")
    if cfg.get("redact_secrets", True):
        for name, pat in _SECRET_PATTERNS:
            if pat.search(s):
                s = pat.sub(f"[REDACTED_{name.upper()}]", s)
                redactions.append(name)
    if cfg.get("redact_pii", True):
        for name, pat in _PII_PATTERNS:
            if pat.search(s):
                s = pat.sub(f"[REDACTED_{name.upper()}]", s)
                redactions.append(name)
    words = s.split()
    if len(words) >= 12:
        uniq_ratio = len(set(w.lower() for w in words)) / len(words)
        if uniq_ratio < 0.2:
            quality_flags.append("repetitive_text")
    return s, sorted(set(redactions)), sorted(set(quality_flags))
def _load_entries_unlocked():
    rows, _, _ = storage.read_jsonl_safe(_feedback_path(), quarantine=True)
    return [r for r in rows if isinstance(r, dict) and r.get("id")]
def _save_entries_unlocked(entries):
    p = _feedback_path()
    payload = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries)
    storage.atomic_write_text(p, payload, fsync=True, backup=True)
def _compute_content_hash(prompt, response, better, chosen, rejected):
    canonical = json.dumps(
        [prompt or "", response or "", better or "", chosen or "", rejected or ""],
        ensure_ascii=False,
