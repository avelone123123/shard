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
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
def _prune_expired_unlocked(entries, cfg):
    days = int(cfg.get("retention_days") or 90)
    cutoff = time.time() - (days * 86400.0)
    kept = []
    removed = 0
    for e in entries:
        st = e.get("status")
        ts = float(e.get("updated_at") or e.get("created_at") or 0)
        if st in ("deleted", "rejected", "pending") and ts > 0 and ts < cutoff:
            removed += 1
            continue
        kept.append(e)
    return kept, removed
def prune_expired_feedback():
    with _LOCK:
        with storage.file_lock(_feedback_path()):
            cfg = get_privacy_config()
            entries = _load_entries_unlocked()
            kept, removed = _prune_expired_unlocked(entries, cfg)
            if removed > 0:
                _save_entries_unlocked(kept)
                _append_audit("feedback_retention_pruned", {"removed": removed, "retention_days": cfg.get("retention_days")})
            return {"removed": removed, "remaining": len(kept), "retention_days": cfg.get("retention_days")}
def submit_feedback(payload, source="local", client_hint=None):
    if not isinstance(payload, dict):
        raise ValueError("Feedback payload must be a JSON object.")
    src = "remote" if str(source).lower() == "remote" else "local"
    with _LOCK:
        with storage.file_lock(_feedback_path()):
            cfg = get_privacy_config()
            if not cfg.get("allow_feedback", True):
                raise PermissionError("Feedback collection is disabled by the server owner.")
            if src == "remote" and not cfg.get("allow_remote_feedback", False):
                raise PermissionError("Remote client feedback contributions are disabled by server privacy policy.")
            consent_given = bool(payload.get("consent_given"))
            if cfg.get("require_explicit_consent", True) and not consent_given:
                raise PermissionError("Explicit user consent (consent_given=true) is required before storing feedback.")
            raw_cid = payload.get("contributor_id") or client_hint or ("local-owner" if src == "local" else "remote-client")
            cid = pseudonymize_contributor(raw_cid)
            if is_contributor_opted_out(cid):
                raise PermissionError("Contributor has opted out and revoked training participation.")
            rating = str(payload.get("rating") or "up").strip().lower()
            if rating not in VALID_RATINGS:
                raise ValueError(f"Invalid rating '{rating}'. Allowed: {', '.join(VALID_RATINGS)}.")
            prompt, r1, f1 = sanitize_and_screen(payload.get("prompt"), cfg, allow_empty=False)
            response, r2, f2 = sanitize_and_screen(payload.get("response"), cfg, allow_empty=(rating == "ab"))
            better, r3, f3 = sanitize_and_screen(payload.get("better_response"), cfg, allow_empty=True)
            chosen, r4, f4 = sanitize_and_screen(payload.get("chosen"), cfg, allow_empty=True)
            rejected, r5, f5 = sanitize_and_screen(payload.get("rejected"), cfg, allow_empty=True)
            if rating == "ab":
                if not chosen or not rejected:
                    raise ValueError("A/B preference feedback requires non-empty 'chosen' and 'rejected' responses.")
                if chosen.strip() == rejected.strip():
                    raise ValueError("A/B preference 'chosen' and 'rejected' must differ.")
            elif rating == "correction" and not better and not chosen:
                raise ValueError("Correction feedback requires 'better_response' or 'chosen'.")
            elif not response and not (chosen and rejected):
                raise ValueError("Feedback requires a non-empty 'response' or A/B pair.")
            if rating in ("down", "correction") and better and response and better.strip() != response.strip():
                if not chosen:
                    chosen = better
                if not rejected:
                    rejected = response
            all_redactions = sorted(set(r1 + r2 + r3 + r4 + r5))
            all_flags = sorted(set(f1 + f2 + f3 + f4 + f5))
            if all_redactions:
                all_flags = sorted(set(all_flags + ["redacted_sensitive_data"]))
            status = "pending" if cfg.get("require_manual_review", True) else "approved"
            now = time.time()
            entry = {
                "id": f"fb-{uuid.uuid4().hex[:12]}",
                "created_at": now,
                "updated_at": now,
                "source": src,
                "contributor_id": cid,
                "model_id": str(payload.get("model_id") or "").strip(),
                "model_version": str(payload.get("model_version") or "base").strip(),
                "rating": rating,
                "prompt": prompt,
                "response": response,
                "better_response": better,
                "chosen": chosen,
                "rejected": rejected,
                "consent_given": consent_given,
                "consent_scope": "local_owner_dataset",
                "status": status,
                "redactions": all_redactions,
                "quality_flags": all_flags,
                "content_hash": _compute_content_hash(prompt, response, better, chosen, rejected),
            }
            storage.append_jsonl_locked(_feedback_path(), entry, fsync=True)
            _append_audit("feedback_submitted", {
                "id": entry["id"],
                "source": src,
                "rating": rating,
                "status": status,
                "redactions": all_redactions,
            })
            return entry
def list_feedback(status=None, limit=200, include_deleted=False):
    with _LOCK:
        with storage.file_lock(_feedback_path()):
            entries = _load_entries_unlocked()
        out = []
        for e in reversed(entries):
            st = e.get("status")
            if st == "deleted" and not include_deleted:
                continue
            if status == "included_in_dataset":
                if not e.get("included_in_datasets"):
                    continue
            elif status and st != status:
                continue
            out.append(e)
            if len(out) >= max(1, int(limit)):
                break
        return out
def list_contributor_submissions(contributor_id, limit=50):
    cid = pseudonymize_contributor(contributor_id)
    with _LOCK:
        with storage.file_lock(_feedback_path()):
            entries = _load_entries_unlocked()
        out = []
        for e in reversed(entries):
            if e.get("contributor_id") != cid:
                continue
            out.append({
                "id": e.get("id"),
                "created_at": e.get("created_at"),
                "rating": e.get("rating"),
                "status": e.get("status"),
                "model_id": e.get("model_id"),
                "redacted": bool(e.get("redactions")),
            })
            if len(out) >= max(1, int(limit)):
                break
        return {
            "contributor_id": cid,
            "opted_out": is_contributor_opted_out(cid),
            "submissions": out,
        }
def review_feedback(entry_id, action, edited_fields=None):
    act = str(action or "").strip().lower()
    if act not in ("approve", "reject", "delete", "edit", "pending"):
        raise ValueError(f"Unsupported review action '{act}'.")
    with _LOCK:
        with storage.file_lock(_feedback_path()):
            cfg = get_privacy_config()
            entries = _load_entries_unlocked()
            target = None
            for e in entries:
                if e.get("id") == entry_id:
                    target = e
                    break
            if target is None:
                raise KeyError(f"Feedback entry '{entry_id}' not found.")
            if target.get("status") == "deleted" and act != "delete":
                raise ValueError("Deleted feedback entries cannot be restored.")
            if edited_fields and isinstance(edited_fields, dict):
                for fld in ("prompt", "response", "better_response", "chosen", "rejected"):
                    if fld in edited_fields:
                        val, r_hits, _ = sanitize_and_screen(
                            edited_fields[fld], cfg, allow_empty=(fld != "prompt")
                        )
                        target[fld] = val
                        if r_hits:
                            target["redactions"] = sorted(set((target.get("redactions") or []) + r_hits))
                target["content_hash"] = _compute_content_hash(
                    target.get("prompt"),
                    target.get("response"),
                    target.get("better_response"),
                    target.get("chosen"),
                    target.get("rejected"),
                )
            if act == "approve":
                target["status"] = "approved"
            elif act == "reject":
                target["status"] = "rejected"
            elif act == "pending":
                target["status"] = "pending"
            elif act == "delete":
                target["status"] = "deleted"
                target["prompt"] = ""
                target["response"] = ""
                target["better_response"] = ""
                target["chosen"] = ""
                target["rejected"] = ""
                target["content_hash"] = None
            target["updated_at"] = time.time()
            _save_entries_unlocked(entries)
            _append_audit("feedback_reviewed", {"id": entry_id, "action": act, "status": target["status"]})
            return target
def revoke_contributor(contributor_id, delete_existing=True):
    cid = pseudonymize_contributor(contributor_id)
    with _LOCK:
        with storage.file_lock(_optouts_path()), storage.file_lock(_feedback_path()):
            opts = _load_optouts()
            opts[cid] = {"revoked_at": time.time()}
            _save_optouts(opts)
            deleted_count = 0
            if delete_existing:
                entries = _load_entries_unlocked()
                changed = False
                for e in entries:
                    if e.get("contributor_id") == cid and e.get("status") != "deleted":
                        e["status"] = "deleted"
                        e["prompt"] = ""
                        e["response"] = ""
                        e["better_response"] = ""
                        e["chosen"] = ""
                        e["rejected"] = ""
                        e["content_hash"] = None
                        e["updated_at"] = time.time()
                        deleted_count += 1
                        changed = True
                if changed:
                    _save_entries_unlocked(entries)
            _append_audit("contributor_revoked", {"contributor_id": cid, "deleted_count": deleted_count})
            return {"contributor_id": cid, "opted_out": True, "deleted_count": deleted_count}
def _entry_to_sft_record(entry):
    prompt = (entry.get("prompt") or "").strip()
    better = (entry.get("better_response") or "").strip()
    chosen = (entry.get("chosen") or "").strip()
    resp = (entry.get("response") or "").strip()
    rating = entry.get("rating")
    assistant_text = ""
    if better:
        assistant_text = better
    elif chosen:
        assistant_text = chosen
    elif rating == "up" and resp:
        assistant_text = resp
    if not prompt or not assistant_text:
        reason = "downvote_without_correction" if rating == "down" else "missing_prompt_or_target"
        return None, reason
    return {
        "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": assistant_text},
        ]
    }, None
def _entry_to_dpo_record(entry):
    prompt = (entry.get("prompt") or "").strip()
    chosen = (entry.get("chosen") or "").strip()
    rejected = (entry.get("rejected") or "").strip()
    if not chosen and entry.get("better_response"):
        chosen = entry["better_response"].strip()
    if not rejected and entry.get("rating") in ("down", "correction") and entry.get("response") and chosen:
        rejected = entry["response"].strip()
    if not prompt or not chosen or not rejected or chosen == rejected:
        return None, "missing_distinct_chosen_rejected_pair"
    return {
        "prompt": prompt,
        "chosen": chosen,
        "rejected": rejected,
    }, None
def build_dataset_snapshot(task="sft", base_model=None, name=None, include_pending=False, validate_with_soup=True):
    task_norm = str(task or "sft").strip().lower()
    if task_norm not in ("sft", "dpo"):
        raise ValueError("Dataset snapshot builder supports 'sft' (chatml) or 'dpo' preference pairs.")
    fmt = "chatml" if task_norm == "sft" else "dpo"
    with _LOCK:
        with storage.file_lock(_feedback_path()), storage.file_lock(_optouts_path()):
            entries = _load_entries_unlocked()
            opts = _load_optouts()
            allowed_statuses = {"approved", "pending"} if include_pending else {"approved"}
            seen_hashes = set()
            rows = []
            skip_reasons = {}
            dedup_removed = 0
            redacted_entries = 0
            total_checked = 0
            local_count = 0
            remote_count = 0
            char_lengths = []
            contrib_ids = set()
            included_entries = []
            for e in entries:
                if e.get("status") not in allowed_statuses:
                    continue
                if e.get("contributor_id") in opts:
                    skip_reasons["revoked_contributor"] = skip_reasons.get("revoked_contributor", 0) + 1
                    continue
                if not e.get("consent_given", False):
                    skip_reasons["missing_consent"] = skip_reasons.get("missing_consent", 0) + 1
                    continue
                total_checked += 1
                rec, reason = _entry_to_sft_record(e) if task_norm == "sft" else _entry_to_dpo_record(e)
                if rec is None:
                    skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                    continue
                canon = json.dumps(rec, sort_keys=True, ensure_ascii=False)
                h = hashlib.sha256(canon.encode("utf-8")).hexdigest()
                if h in seen_hashes:
                    dedup_removed += 1
                    continue
                seen_hashes.add(h)
                rows.append(rec)
                included_entries.append(e)
                if e.get("contributor_id"):
                    contrib_ids.add(e["contributor_id"])
                if e.get("redactions"):
                    redacted_entries += 1
                if e.get("source") == "remote":
                    remote_count += 1
                else:
                    local_count += 1
                char_lengths.append(len(canon))
            if not rows:
                raise ValueError(
                    f"No eligible records to build {task_norm.upper()} dataset (checked={total_checked}, skips={skip_reasons})."
                )
            ds_dir = _datasets_dir()
            ts_tag = time.strftime("%Y%m%d-%H%M%S")
            slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", (name or f"{task_norm}-{ts_tag}")).strip("-") or f"{task_norm}-{ts_tag}"
            snap_id = f"ds-{slug}-{uuid.uuid4().hex[:6]}"
            jsonl_path = ds_dir / f"{snap_id}.jsonl"
            payload_text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
            storage.atomic_write_text(jsonl_path, payload_text, fsync=True, backup=False)
            file_sha = storage.sha256_file(jsonl_path)
            soup_val = None
            if validate_with_soup:
                soup_info = soup_adapter.detect_soup(probe_deps=False)
                if soup_info.get("available"):
                    soup_val = soup_adapter.validate_dataset(soup_info, str(jsonl_path), fmt=fmt)
                    if soup_val and not soup_val.get("ok"):
                        try:
                            jsonl_path.unlink()
                        except Exception:
                            pass
                        raise ValueError(
                            f"SOUP dataset validation failed: {(soup_val.get('stderr') or soup_val.get('stdout') or 'invalid schema')[:240]}"
                        )
            rel_path = storage.normalize_rel_path(str(jsonl_path), paths.data_dir())
            rejected_cnt = sum(int(v) for v in (skip_reasons or {}).values()) if isinstance(skip_reasons, dict) else 0
            val_status = "soup_verified" if (soup_val and soup_val.get("ok")) else "schema_valid"
            manifest = {
                "snapshot_id": snap_id,
                "name": name or snap_id,
                "task": task_norm,
                "format": fmt,
                "base_model": base_model,
                "path": str(jsonl_path),
                "rel_path": rel_path,
                "created_at": time.time(),
                "record_count": len(rows),
                "valid_records": len(rows),
                "rejected_records": rejected_cnt,
                "source_feedback_count": total_checked,
                "validation_status": val_status,
                "total_approved_checked": total_checked,
                "dedup_removed": dedup_removed,
                "skip_reasons": skip_reasons,
                "redacted_entries": redacted_entries,
                "sources": {"local": local_count, "remote": remote_count},
                "source_contributors": sorted(contrib_ids),
                "avg_record_chars": round(sum(char_lengths) / len(char_lengths), 1) if char_lengths else 0,
                "min_record_chars": min(char_lengths) if char_lengths else 0,
                "sha256": file_sha,
                "soup_validation": soup_val,
                "creation_summary": {
                    "source_feedback": total_checked,
                    "valid_records": len(rows),
                    "rejected_records": rejected_cnt,
                    "dedup_removed": dedup_removed,
                    "skip_reasons": skip_reasons,
                    "validation_status": val_status,
                },
            }
            manifest_path = ds_dir / f"{snap_id}.manifest.json"
            try:
                storage.atomic_write_json(manifest_path, manifest, fsync=True, backup=False)
            except Exception:
                try:
                    if jsonl_path.exists():
                        jsonl_path.unlink()
                except Exception:
                    pass
                raise
            if included_entries:
                for inc_e in included_entries:
                    ds_ids = list(inc_e.get("included_in_datasets") or [])
                    if snap_id not in ds_ids:
                        ds_ids.append(snap_id)
                    inc_e["included_in_datasets"] = ds_ids
                _save_entries_unlocked(entries)
            _append_audit("dataset_snapshot_created", {
                "snapshot_id": snap_id,
                "task": task_norm,
                "record_count": len(rows),
                "sha256": file_sha[:16],
            })
            return manifest
def _hydrate_manifest(m, opts=None):
    if not isinstance(m, dict):
        return m
    resolved = storage.resolve_portable_path(m.get("path"), m.get("rel_path"), paths.data_dir())
    m["path"] = resolved
    exists = bool(resolved and os.path.isfile(resolved))
    m["exists"] = exists
    expected_sha = m.get("sha256")
    integrity_ok = False
    if exists and expected_sha:
        try:
            integrity_ok = storage.sha256_file(resolved) == expected_sha
        except Exception:
            integrity_ok = False
    m["integrity_ok"] = integrity_ok
    if opts is None:
        opts = _load_optouts()
    contribs = set(m.get("source_contributors") or [])
    m["tainted_revoked"] = bool(contribs & set(opts.keys()))
    return m
def list_datasets():
    ds_dir = _datasets_dir()
    opts = _load_optouts()
    out = []
    for p in sorted(ds_dir.glob("*.manifest.json"), reverse=True):
        try:
            m = storage.read_json_safe(p, default=None, expected_type=dict, quarantine=False, use_backup=False)
            if isinstance(m, dict) and m.get("snapshot_id"):
                out.append(_hydrate_manifest(m, opts=opts))
        except Exception:
            continue
    out.sort(key=lambda x: float(x.get("created_at") or 0), reverse=True)
    return out
def get_dataset_manifest(snapshot_id):
    if not snapshot_id or "/" in snapshot_id or "\\" in snapshot_id or ".." in snapshot_id:
        raise ValueError("Invalid snapshot_id.")
    mp = _datasets_dir() / f"{snapshot_id}.manifest.json"
    if not mp.is_file():
        raise FileNotFoundError(f"Dataset snapshot '{snapshot_id}' not found.")
    m = storage.read_json_safe(mp, default=None, expected_type=dict, quarantine=True, use_backup=False)
    if not m:
        raise ValueError(f"Corrupted dataset manifest '{snapshot_id}'.")
    return _hydrate_manifest(m)
def verify_dataset_integrity(snapshot_id):
    m = get_dataset_manifest(snapshot_id)
    if not m.get("exists"):
        raise FileNotFoundError(f"Dataset file missing for snapshot '{snapshot_id}'.")
    if not m.get("integrity_ok"):
        raise ValueError(f"Dataset checksum verification failed for '{snapshot_id}' (corrupted or modified file).")
    return m
def preview_dataset_records(snapshot_id, limit=10):
    m = get_dataset_manifest(snapshot_id)
    p = Path(m["path"])
    if not p.is_file():
        return []
    rows = []
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        for ln in f:
            if not ln.strip():
                continue
            try:
                rows.append(json.loads(ln))
            except Exception:
                continue
            if len(rows) >= max(1, int(limit)):
                break
    return rows
def _extract_structured_record(obj, expected_fmt, line_no, idx):
    if not isinstance(obj, dict):
        return {
            "index": idx,
            "line_number": line_no,
            "prompt": "",
            "chosen": "",
            "rejected": "",
            "record_type": "invalid",
            "validation_status": "invalid",
            "validation_error": "Record is not a JSON object",
            "raw": obj,
        }
    fmt_l = str(expected_fmt or "").strip().lower()
    prompt_txt = ""
    chosen_txt = ""
    rejected_txt = ""
    rec_type = fmt_l or "unknown"
    err = None
    if "chosen" in obj or "rejected" in obj:
        rec_type = "dpo"
        prompt_txt = str(obj.get("prompt") or "")
        chosen_txt = str(obj.get("chosen") or "")
        rejected_txt = str(obj.get("rejected") or "")
        if not prompt_txt.strip() or not chosen_txt.strip() or not rejected_txt.strip():
            err = "DPO record requires non-empty prompt, chosen, and rejected"
        elif chosen_txt.strip() == rejected_txt.strip():
            err = "DPO chosen and rejected responses are identical"
    elif isinstance(obj.get("messages"), list):
        rec_type = "sft_chatml"
        msgs = obj.get("messages") or []
        prompts = []
        replies = []
        valid_msgs = True
        for msg in msgs:
            if not isinstance(msg, dict) or not msg.get("role") or msg.get("content") is None:
                valid_msgs = False
                break
            role = str(msg.get("role")).strip().lower()
            content = str(msg.get("content") or "")
            if role in ("system", "user"):
                prompts.append(f"[{role}] {content}" if role == "system" else content)
            elif role == "assistant":
                replies.append(content)
        prompt_txt = "\n".join(p for p in prompts if p)
        chosen_txt = "\n".join(r for r in replies if r)
        if not valid_msgs or not msgs:
            err = "SFT messages must be non-empty list of {role, content} objects"
        elif not prompt_txt.strip() or not chosen_txt.strip():
            err = "SFT record must include user prompt and assistant response"
    elif "instruction" in obj or "output" in obj:
        rec_type = "sft_alpaca"
        instr = str(obj.get("instruction") or "")
        inp = str(obj.get("input") or "")
        prompt_txt = f"{instr}\n{inp}".strip() if inp else instr
        chosen_txt = str(obj.get("output") or "")
        if not prompt_txt.strip() or not chosen_txt.strip():
            err = "Alpaca record requires instruction and output"
    elif "prompt" in obj or "completion" in obj or "response" in obj or "text" in obj:
        rec_type = "sft_text"
        prompt_txt = str(obj.get("prompt") or "")
        chosen_txt = str(obj.get("completion") or obj.get("response") or obj.get("text") or "")
        if not chosen_txt.strip():
            err = "Record has empty response/text"
    else:
        err = "Unrecognized dataset record schema"
    if not err and fmt_l == "dpo" and rec_type != "dpo":
        err = f"Dataset expects DPO format, found {rec_type}"
    return {
        "index": idx,
        "line_number": line_no,
        "prompt": prompt_txt,
        "chosen": chosen_txt,
        "rejected": rejected_txt,
        "record_type": rec_type,
        "validation_status": "invalid" if err else "valid",
        "validation_error": err or "",
        "raw": obj,
    }
def preview_dataset_paginated(snapshot_id, page=1, page_size=20):
    m = get_dataset_manifest(snapshot_id)
    p = Path(m["path"])
    page_num = max(1, int(page or 1))
    size_num = max(1, min(100, int(page_size or 20)))
    if not p.is_file():
        return {
            "snapshot_id": snapshot_id,
            "dataset": m,
            "page": page_num,
            "page_size": size_num,
            "total_records": 0,
            "valid_count": 0,
            "invalid_count": 0,
            "total_pages": 1,
            "records": [],
            "error": "Dataset JSONL file missing on disk",
        }
    fmt_hint = m.get("format") or m.get("task") or "chatml"
    records = []
    valid_count = 0
    invalid_count = 0
    start_idx = (page_num - 1) * size_num
    end_idx = start_idx + size_num
    idx = 0
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        for line_no, ln in enumerate(f, start=1):
            stripped = ln.strip()
            if not stripped:
                continue
            item = None
            try:
                parsed = json.loads(stripped)
                item = _extract_structured_record(parsed, fmt_hint, line_no, idx)
            except Exception as e:
                item = {
                    "index": idx,
                    "line_number": line_no,
                    "prompt": stripped[:200],
                    "chosen": "",
                    "rejected": "",
                    "record_type": "corrupted",
                    "validation_status": "invalid",
                    "validation_error": f"Malformed JSON: {e}",
                    "raw": {"raw_line": stripped[:500]},
                }
            if item["validation_status"] == "valid":
                valid_count += 1
            else:
                invalid_count += 1
            if start_idx <= idx < end_idx:
                records.append(item)
            idx += 1
    total_records = idx
    total_pages = max(1, (total_records + size_num - 1) // size_num)
    return {
        "snapshot_id": snapshot_id,
        "dataset": m,
        "page": page_num,
        "page_size": size_num,
        "total_records": total_records,
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "total_pages": total_pages,
        "records": records,
    }
def validate_dataset_snapshot(snapshot_id, task=None, check_with_soup=False):
    errors = []
    warnings = []
    try:
        m = verify_dataset_integrity(snapshot_id)
    except Exception as e:
        return {
            "ok": False,
            "snapshot_id": snapshot_id,
            "format": None,
            "task": task,
            "total_lines": 0,
            "valid_records": 0,
            "invalid_records": 0,
            "errors": [str(e)],
            "warnings": [],
            "soup_validation": None,
        }
    p = Path(m["path"])
    fmt_hint = m.get("format") or m.get("task") or "chatml"
    task_norm = str(task or m.get("task") or "sft").strip().lower()
    if task_norm == "dpo" and str(m.get("format") or "").lower() != "dpo":
        errors.append("Задача DPO требует датасет в формате dpo (prompt/chosen/rejected).")
    total_lines = 0
    valid_records = 0
    invalid_records = 0
    sample_errors = []
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        for line_no, ln in enumerate(f, start=1):
            stripped = ln.strip()
            if not stripped:
                continue
            total_lines += 1
            try:
                parsed = json.loads(stripped)
                item = _extract_structured_record(parsed, "dpo" if task_norm == "dpo" else fmt_hint, line_no, total_lines - 1)
                if item["validation_status"] == "valid":
                    valid_records += 1
                else:
                    invalid_records += 1
                    if len(sample_errors) < 5:
                        sample_errors.append(f"Line {line_no}: {item['validation_error']}")
            except Exception as e:
                invalid_records += 1
                if len(sample_errors) < 5:
                    sample_errors.append(f"Line {line_no}: invalid JSON ({e})")
    if valid_records == 0:
        errors.append("В датасете нет ни одной валидной записи для обучения.")
    elif valid_records < 5:
        warnings.append(f"В датасете всего {valid_records} записей; для стабильного LoRA-обучения рекомендуется минимум 10–20 примеров.")
    if invalid_records > 0:
        errors.append(f"Найдено поврежденных или некорректных записей: {invalid_records}. " + "; ".join(sample_errors))
    soup_val = None
    if check_with_soup and not errors:
        soup_info = soup_adapter.detect_soup(probe_deps=False)
        if soup_info.get("available"):
            soup_val = soup_adapter.validate_dataset(soup_info, str(p), fmt=fmt_hint)
            if soup_val and not soup_val.get("ok"):
                errors.append(f"SOUP data validate не прошел: {(soup_val.get('stderr') or soup_val.get('stdout') or 'schema error')[:240]}")
    return {
        "ok": len(errors) == 0,
        "snapshot_id": snapshot_id,
        "format": fmt_hint,
        "task": task_norm,
        "total_lines": total_lines,
        "valid_records": valid_records,
        "invalid_records": invalid_records,
        "errors": errors,
        "warnings": warnings,
        "soup_validation": soup_val,
    }
def compute_stats():
    with _LOCK:
        with storage.file_lock(_feedback_path()):
            entries = _load_entries_unlocked()
        counts = {"total": 0, "pending": 0, "approved": 0, "rejected": 0, "deleted": 0, "included_in_dataset": 0}
        ratings = {"up": 0, "down": 0, "correction": 0, "ab": 0}
        sources = {"local": 0, "remote": 0}
        sft_eligible = 0
        dpo_eligible = 0
        redacted_count = 0
        for e in entries:
            counts["total"] += 1
            st = e.get("status", "pending")
            if st in counts:
                counts[st] += 1
            if st == "deleted":
                continue
            if e.get("included_in_datasets"):
                counts["included_in_dataset"] += 1
            rt = e.get("rating", "up")
            if rt in ratings:
                ratings[rt] += 1
            src = e.get("source", "local")
            if src in sources:
                sources[src] += 1
            if e.get("redactions"):
                redacted_count += 1
            if st == "approved":
                s_rec, _ = _entry_to_sft_record(e)
                if s_rec is not None:
                    sft_eligible += 1
                d_rec, _ = _entry_to_dpo_record(e)
                if d_rec is not None:
                    dpo_eligible += 1
        datasets = list_datasets()
        return {
            "counts": counts,
            "ratings": ratings,
            "sources": sources,
            "redacted_entries": redacted_count,
            "eligible_approved": {"sft": sft_eligible, "dpo": dpo_eligible},
            "dataset_count": len(datasets),
            "opted_out_contributors": len(_load_optouts()),
        }
