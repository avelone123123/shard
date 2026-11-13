import copy
import hashlib
import hmac
import inspect
import json
import secrets
import threading
import time
from collections import deque
from urllib.parse import quote
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from . import net_identity
_MAX_BODY_BYTES = 65536
_ALLOWED_SORT = {"activity", "participants", "users", "popular", "slots", "capacity", "load", "newest", "recent", "name"}
_VALID_HEX = frozenset("0123456789abcdef")
_FORBIDDEN_HINT_KEYS = frozenset({"invite_id", "secret", "token", "private_key", "privkey", "password", "listing", "name", "models", "prompt", "messages"})
def commitment_for_invite(invite_id):
    raw = str(invite_id or "").strip()
    if not raw:
        raise ValueError("empty_invite_id")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
def _is_sha256_hex(val):
    s = str(val or "").strip().lower()
    return len(s) == 64 and all(c in _VALID_HEX for c in s)
def _is_int(val):
    return isinstance(val, int) and not isinstance(val, bool)
def _to_nonneg_int(val):
    if isinstance(val, bool):
        return None
    try:
        iv = int(val)
    except Exception:
        return None
    return iv if iv >= 0 else None
def _extract_body(envelope):
    if not isinstance(envelope, dict):
        return None
    if "body" in envelope:
        return envelope.get("body")
    if "payload" in envelope:
        return envelope.get("payload")
    return {}
def _extract_seq(envelope, body):
    raw = envelope.get("seq")
    if raw is None and isinstance(body, dict):
        raw = body.get("seq")
    if not _is_int(raw) or raw < 0:
        return None
    return raw
def _extract_listing_and_ttl(envelope, body):
    if isinstance(body, dict) and isinstance(body.get("listing"), dict):
        raw_listing = body["listing"]
        ttl_raw = body.get("ttl_s", raw_listing.get("ttl_s"))
        return raw_listing, ttl_raw
    if isinstance(body, dict) and body:
        return body, body.get("ttl_s")
    if isinstance(envelope.get("listing"), dict):
        raw_listing = envelope["listing"]
        ttl_raw = envelope.get("ttl_s", raw_listing.get("ttl_s"))
        return raw_listing, ttl_raw
    return None, None
def _resolve_expected_action(envelope, target_domain, default_action):
    domains = getattr(net_identity, "_ACTION_DOMAINS", None)
    act = envelope.get("action") if isinstance(envelope, dict) else None
    if isinstance(domains, dict):
        if isinstance(act, str) and domains.get(act) == target_domain:
            return act
        for k, v in domains.items():
            if v == target_domain:
                return k
    if isinstance(target_domain, str) and target_domain:
        return target_domain
    return default_action
def _action_for_domain(target_domain, fallback):
    domains = getattr(net_identity, "_ACTION_DOMAINS", None)
    if isinstance(domains, dict):
        if fallback in domains and domains[fallback] == target_domain:
            return fallback
        for k, v in domains.items():
            if v == target_domain:
                return k
    if isinstance(target_domain, str) and target_domain:
        return target_domain
    return fallback
def _resolve_private_expected_action(envelope):
    domains = getattr(net_identity, "_ACTION_DOMAINS", None)
    act = envelope.get("action") if isinstance(envelope, dict) else None
    relay_dom = getattr(net_identity, "DOMAIN_RELAY_REG", None)
    reg_dom = getattr(net_identity, "DOMAIN_DISC_REG", None)
    if isinstance(domains, dict) and isinstance(act, str):
        dom = domains.get(act)
        if dom is not None and dom in (relay_dom, reg_dom):
            return act
    return _action_for_domain(relay_dom, "relay_register")
def _sanitize_hb_metrics(body):
    out = {}
    if not isinstance(body, dict):
        return out
    if body.get("active_model") is not None:
        out["active_model"] = str(body["active_model"]).strip()[:160]
    if body.get("status") is not None:
        st = str(body["status"]).strip().lower()[:32]
        if st:
            out["status"] = st
    for key in ("slots_available", "participant_count", "max_participants"):
        if body.get(key) is not None:
            iv = _to_nonneg_int(body[key])
            if iv is not None:
                out[key] = iv
    if body.get("load") is not None and not isinstance(body["load"], bool):
        try:
            lv = float(body["load"])
            if lv == lv and lv != float("inf") and lv != float("-inf"):
                out["load"] = round(max(0.0, lv), 4)
        except Exception:
            pass
    return out
def _sanitize_private_hint(hint):
    if hint is None:
        return None
    if isinstance(hint, str):
        s = hint.strip()
        if not s or len(s) > 4096:
            return None
        return s
    if isinstance(hint, dict):
        for k in hint:
            if str(k).strip().lower() in _FORBIDDEN_HINT_KEYS:
                return None
        try:
            encoded = json.dumps(hint, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except Exception:
            return None
        if len(encoded) > 4096:
            return None
        return copy.deepcopy(hint)
    return None
def _effective_access(listing):
    raw_acc = listing.get("access_mode") or listing.get("access")
    if isinstance(raw_acc, str) and raw_acc.strip():
        return raw_acc.strip().lower()
    if listing.get("requires_invite") is True or listing.get("open") is False:
        return "invite"
    if listing.get("open") is True or listing.get("requires_invite") is False:
        return "open"
    return ""
def _listing_search_blob(rec):
    listing = rec.get("listing") or {}
    parts = [
        str(rec.get("node_id") or ""),
        str(listing.get("name") or ""),
        str(listing.get("description") or ""),
        str(listing.get("region") or ""),
        str(listing.get("active_model") or ""),
        str(listing.get("model") or ""),
        str(_effective_access(listing)),
        str(listing.get("host_name") or listing.get("owner") or ""),
        str(listing.get("network_name") or ""),
    ]
    for key in ("models", "supported_models", "tags", "capabilities"):
        val = listing.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    parts.append(str(item.get("id") or item.get("name") or ""))
                else:
                    parts.append(str(item or ""))
    return " ".join(parts).lower()
def _listing_matches(rec, q="", model="", region="", access=""):
    listing = rec.get("listing") or {}
    q_s = str(q or "").strip().lower()
    if q_s and q_s not in _listing_search_blob(rec):
        return False
    m_s = str(model or "").strip().lower()
    if m_s:
        candidates = []
        for mk in ("active_model", "model"):
            if listing.get(mk):
                candidates.append(str(listing[mk]).lower())
        for key in ("models", "supported_models"):
            vals = listing.get(key)
            if isinstance(vals, list):
                for item in vals:
                    if isinstance(item, dict):
                        candidates.append(str(item.get("id") or item.get("name") or "").lower())
                    else:
                        candidates.append(str(item or "").lower())
        if not any(m_s in c for c in candidates if c):
            return False
    r_s = str(region or "").strip().lower()
    if r_s:
        reg = str(listing.get("region") or "").strip().lower()
        if r_s not in reg:
            return False
    a_s = str(access or "").strip().lower()
    if a_s:
        acc = _effective_access(listing)
        if a_s in ("invite", "invite_only", "invite-only"):
            if acc not in ("invite", "invite_only", "invite-only"):
                return False
        elif acc != a_s:
            return False
    return True
def _sort_key_for_server(rec, sort_mode):
    listing = rec.get("listing") or {}
    mode = str(sort_mode or "activity").strip().lower()
    if mode not in _ALLOWED_SORT:
        mode = "activity"
    updated = float(rec.get("updated_at") or 0.0)
    reg_at = float(rec.get("registered_at") or 0.0)
    participants = int(listing.get("participant_count") or 0)
    slots = int(listing.get("slots_available") or 0)
    try:
        load = float(listing.get("load") if listing.get("load") is not None else 0.0)
    except Exception:
        load = 0.0
    name = str(listing.get("name") or rec.get("node_id") or "").lower()
    nid = str(rec.get("node_id") or "")
    if mode in ("participants", "users", "popular"):
        return (-participants, -updated, -slots, load, nid)
    if mode in ("slots", "capacity"):
        return (-slots, load, -updated, nid)
    if mode == "load":
        return (load, -slots, -updated, nid)
    if mode in ("newest", "recent"):
        return (-reg_at, -updated, nid)
    if mode == "name":
        return (name, -updated, nid)
    return (-updated, -participants, -slots, load, nid)
class _RateLimiter:
    def __init__(self, limit=120, window_s=60.0, max_keys=8192):
        self.limit = max(0, int(limit))
        self.window_s = max(1.0, float(window_s))
        self.max_keys = max(16, int(max_keys))
        self._hits = {}
        self._lock = threading.Lock()
    def check(self, key, record=True):
        if self.limit <= 0:
            return False, int(self.window_s)
        now = time.monotonic()
        k = str(key or "unknown")
        cutoff = now - self.window_s
        with self._lock:
            if k not in self._hits and len(self._hits) >= self.max_keys:
                stale = [ek for ek, dq in self._hits.items() if not dq or dq[-1] < cutoff]
                for ek in stale:
                    self._hits.pop(ek, None)
                while len(self._hits) >= self.max_keys:
                    oldest = next(iter(self._hits))
                    self._hits.pop(oldest, None)
            q = self._hits.pop(k, None)
            if q is None:
                q = deque()
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.limit:
                self._hits[k] = q
                retry = max(1, int(self.window_s - (now - q[0])))
                return False, retry
            if record:
                q.append(now)
            self._hits[k] = q
            return True, 0
    def allow(self, key):
        return self.check(key, record=True)
    def reset(self):
        with self._lock:
            self._hits.clear()
class DiscoveryStore:
    def __init__(self, default_ttl=60, min_ttl=15, max_ttl=120, max_records=5000, nonce_ttl_s=180, max_skew_s=60, max_private_records=5000, clock=None):
        self.default_ttl = int(default_ttl)
        self.min_ttl = int(min_ttl)
        self.max_ttl = int(max_ttl)
        self.max_records = max(1, int(max_records))
        self.nonce_ttl_s = max(1, int(nonce_ttl_s))
        self.max_skew_s = max(1, int(max_skew_s))
        self.max_private_records = max(1, int(max_private_records))
        self._clock = clock
        self._lock = threading.RLock()
        self._records = {}
        self._nonces = {}
        self._seq_watermarks = {}
        self._private = {}
        self._private_seq_watermarks = {}
    def _now(self):
        return float(self._clock() if self._clock is not None else time.time())
    def _clamp_ttl(self, raw_ttl, allow_short=False):
        low = 1 if allow_short else self.min_ttl
        if raw_ttl is None or isinstance(raw_ttl, bool):
            return max(low, min(self.max_ttl, self.default_ttl))
        try:
            val = int(float(raw_ttl))
        except Exception:
            return max(low, min(self.max_ttl, self.default_ttl))
        return max(low, min(self.max_ttl, val))
    def _prune_locked(self, now):
        expired_nodes = [nid for nid, rec in self._records.items() if rec["expires_at"] <= now]
        for nid in expired_nodes:
            self._records.pop(nid, None)
        expired_nonces = [nk for nk, exp in self._nonces.items() if exp <= now]
        for nk in expired_nonces:
            self._nonces.pop(nk, None)
        expired_wm = [nid for nid, (_, exp) in self._seq_watermarks.items() if exp <= now and nid not in self._records]
        for nid in expired_wm:
            self._seq_watermarks.pop(nid, None)
        expired_priv = [cm for cm, rec in self._private.items() if rec["expires_at"] <= now]
        for cm in expired_priv:
            self._private.pop(cm, None)
        expired_pwm = [nid for nid, (_, exp) in self._private_seq_watermarks.items() if exp <= now]
        for nid in expired_pwm:
            self._private_seq_watermarks.pop(nid, None)
        return len(expired_nodes)
    def prune_expired(self, now=None):
        with self._lock:
            cur = self._now() if now is None else float(now)
            return self._prune_locked(cur)
    def count(self):
        with self._lock:
            self._prune_locked(self._now())
            return len(self._records)
    def _record_nonce_locked(self, nonce_key, now):
        max_nonces = max(10000, self.max_records * 4)
        while len(self._nonces) >= max_nonces:
            oldest = next(iter(self._nonces))
            self._nonces.pop(oldest, None)
        self._nonces[nonce_key] = now + self.nonce_ttl_s
    def _verify_signed_request(self, envelope, expected_action, now, private_scope=False):
        if not isinstance(envelope, dict):
            return False, 400, {"error": "invalid_envelope"}, None, None, None, None
        ok, reason = net_identity.verify_envelope(envelope, expected_action=expected_action, max_skew_s=self.max_skew_s)
        if not ok:
            return False, 403, {"error": str(reason or "invalid_signature")}, None, None, None, None
        node_id = str(envelope.get("node_id") or "").strip()
        if not node_id or len(node_id) > 128:
            return False, 403, {"error": "invalid_node_id"}, None, None, None, None
        body = _extract_body(envelope)
        if body is None:
            body = {}
        if not isinstance(body, dict):
            return False, 400, {"error": "invalid_body"}, None, None, None, None
        b_nid = body.get("node_id")
        if b_nid is not None and str(b_nid).strip() and str(b_nid).strip() != node_id:
            return False, 403, {"error": "node_id_mismatch"}, None, None, None, None
        seq = _extract_seq(envelope, body)
        if seq is None:
            return False, 400, {"error": "invalid_seq"}, None, None, None, None
        raw_nonce = envelope.get("nonce")
        if raw_nonce is None:
            raw_nonce = body.get("nonce")
        scope = "priv" if private_scope else "pub"
        if raw_nonce is not None and str(raw_nonce).strip():
            nonce_str = str(raw_nonce).strip()
            if len(nonce_str) > 128:
                return False, 400, {"error": "invalid_nonce"}, None, None, None, None
            nonce_key = (scope, node_id, "n:" + nonce_str)
        else:
            sig_tok = str(envelope.get("sig") or f"{expected_action}:{seq}")
            nonce_key = (scope, node_id, "s:" + sig_tok)
        if nonce_key in self._nonces and self._nonces[nonce_key] > now:
            return False, 409, {"error": "nonce_replay"}, None, None, None, None
        wm_map = self._private_seq_watermarks if private_scope else self._seq_watermarks
        last_seq = None
        if not private_scope and node_id in self._records:
            last_seq = self._records[node_id]["last_seq"]
        elif node_id in wm_map:
            last_seq = wm_map[node_id][0]
        if last_seq is not None and seq <= last_seq:
            return False, 409, {"error": "seq_rollback"}, None, None, None, None
        return True, 200, None, node_id, nonce_key, seq, body
    def _public_view(self, rec, now):
        return {
            "node_id": rec["node_id"],
            "listing": copy.deepcopy(rec["listing"]),
            "envelope": copy.deepcopy(rec["envelope"]),
            "last_hb_envelope": copy.deepcopy(rec["last_hb_envelope"]),
            "last_seq": rec["last_seq"],
            "registered_at": rec["registered_at"],
            "updated_at": rec["updated_at"],
            "expires_at": rec["expires_at"],
            "ttl_remaining_s": max(0, int(rec["expires_at"] - now)),
        }
    def register(self, envelope):
        with self._lock:
            now = self._now()
            self._prune_locked(now)
            expected = _resolve_expected_action(envelope, getattr(net_identity, "DOMAIN_DISC_REG", None), "register")
            ok, code, err, node_id, nonce_key, seq, body = self._verify_signed_request(envelope, expected, now)
            if not ok:
                return code, err
            raw_listing, ttl_raw = _extract_listing_and_ttl(envelope, body)
            if not isinstance(raw_listing, dict):
                return 400, {"error": "missing_listing"}
            l_nid = raw_listing.get("node_id")
            if l_nid is not None and str(l_nid).strip() and str(l_nid).strip() != node_id:
                return 403, {"error": "node_id_mismatch"}
            vis = str(raw_listing.get("visibility") or "").strip().lower()
            if vis != "public":
                return 400, {"error": "public_visibility_required"}
            if node_id not in self._records and len(self._records) >= self.max_records:
                return 429, {"error": "directory_full"}
            sanitized = net_identity.sanitize_public_listing(copy.deepcopy(raw_listing))
            if not isinstance(sanitized, dict):
                return 400, {"error": "invalid_listing"}
            if str(sanitized.get("visibility") or "public").strip().lower() != "public":
                return 400, {"error": "public_visibility_required"}
            sanitized["visibility"] = "public"
            sanitized["node_id"] = node_id
            ttl_s = self._clamp_ttl(ttl_raw)
            expires_at = now + ttl_s
            prev = self._records.get(node_id)
            registered_at = prev["registered_at"] if prev is not None else now
            self._record_nonce_locked(nonce_key, now)
            self._seq_watermarks[node_id] = (seq, now + max(self.nonce_ttl_s, ttl_s))
            self._records[node_id] = {
                "node_id": node_id,
                "envelope": copy.deepcopy(envelope),
                "listing": sanitized,
                "last_hb_envelope": None,
                "last_seq": seq,
                "registered_at": registered_at,
                "updated_at": now,
                "expires_at": expires_at,
                "ttl_s": ttl_s,
            }
            return 200, {"status": "ok", "node_id": node_id, "ttl_s": ttl_s, "expires_at": expires_at, "seq": seq}
    def heartbeat(self, envelope):
        with self._lock:
            now = self._now()
            self._prune_locked(now)
            expected = _resolve_expected_action(envelope, getattr(net_identity, "DOMAIN_DISC_HB", None), "heartbeat")
            ok, code, err, node_id, nonce_key, seq, body = self._verify_signed_request(envelope, expected, now)
            if not ok:
                return code, err
            rec = self._records.get(node_id)
            if rec is None:
                return 404, {"error": "node_not_found"}
            ttl_raw = body.get("ttl_s", rec.get("ttl_s"))
            ttl_s = self._clamp_ttl(ttl_raw)
            metrics = _sanitize_hb_metrics(body)
            rec["listing"].update(metrics)
            rec["last_hb_envelope"] = copy.deepcopy(envelope)
            rec["last_seq"] = seq
            rec["updated_at"] = now
            rec["ttl_s"] = ttl_s
            rec["expires_at"] = now + ttl_s
            self._record_nonce_locked(nonce_key, now)
            self._seq_watermarks[node_id] = (seq, now + max(self.nonce_ttl_s, ttl_s))
            return 200, {"status": "ok", "node_id": node_id, "ttl_s": ttl_s, "expires_at": rec["expires_at"], "seq": seq}
    def unregister(self, envelope):
        with self._lock:
            now = self._now()
            self._prune_locked(now)
            expected = _resolve_expected_action(envelope, getattr(net_identity, "DOMAIN_DISC_UNREG", None), "unregister")
            ok, code, err, node_id, nonce_key, seq, _body = self._verify_signed_request(envelope, expected, now)
            if not ok:
                return code, err
            if node_id not in self._records:
                return 404, {"error": "node_not_found"}
            self._record_nonce_locked(nonce_key, now)
            self._seq_watermarks[node_id] = (seq, now + self.nonce_ttl_s)
            self._records.pop(node_id, None)
            return 200, {"status": "ok", "node_id": node_id, "removed": True}
    def list_servers(self, q="", model="", region="", access="", online_only=True, sort="activity", limit=100):
        with self._lock:
            now = self._now()
            self._prune_locked(now)
            lim = max(1, min(500, int(limit or 100)))
            matched = []
            for rec in self._records.values():
                if rec["expires_at"] <= now:
                    continue
                if online_only and str((rec.get("listing") or {}).get("status") or "online").strip().lower() == "offline":
                    continue
                if _listing_matches(rec, q=q, model=model, region=region, access=access):
                    matched.append(self._public_view(rec, now))
            matched.sort(key=lambda r: _sort_key_for_server(r, sort))
            return matched[:lim]
    def lookup(self, node_id):
        nid = str(node_id or "").strip()
        if not nid or len(nid) > 128:
            return None
        with self._lock:
            now = self._now()
            self._prune_locked(now)
            rec = self._records.get(nid)
            if rec is None or rec["expires_at"] <= now:
                return None
            return self._public_view(rec, now)
    def publish_private_commitment(self, envelope):
        with self._lock:
            now = self._now()
            self._prune_locked(now)
            expected = _resolve_private_expected_action(envelope)
            ok, code, err, node_id, nonce_key, seq, body = self._verify_signed_request(envelope, expected, now, private_scope=True)
            if not ok:
                return code, err
            if "listing" in body:
                return 400, {"error": "invalid_private_commitment_payload"}
            commitment = str(body.get("commitment") or "").strip().lower()
            if not _is_sha256_hex(commitment):
                return 400, {"error": "invalid_commitment"}
            raw_hint = body.get("hint")
            if raw_hint is None:
                raw_hint = body.get("rendezvous")
            if raw_hint is None:
                raw_hint = body.get("ciphertext")
            hint = _sanitize_private_hint(raw_hint)
            if hint is None:
                return 400, {"error": "invalid_rendezvous_hint"}
            if commitment not in self._private and len(self._private) >= self.max_private_records:
                return 429, {"error": "private_directory_full"}
            ttl_s = self._clamp_ttl(body.get("ttl_s"), allow_short=True)
            expires_at = now + ttl_s
            self._record_nonce_locked(nonce_key, now)
            self._private_seq_watermarks[node_id] = (seq, now + max(self.nonce_ttl_s, ttl_s))
            self._private[commitment] = {
                "commitment": commitment,
                "node_id": node_id,
                "hint": hint,
                "envelope": copy.deepcopy(envelope),
                "seq": seq,
                "published_at": now,
                "expires_at": expires_at,
                "ttl_s": ttl_s,
            }
            return 200, {"status": "ok", "commitment": commitment, "ttl_s": ttl_s, "expires_at": expires_at}
    def lookup_private_commitment(self, commitment):
        cm = str(commitment or "").strip().lower()
        if not _is_sha256_hex(cm):
            return None
        with self._lock:
            now = self._now()
            self._prune_locked(now)
            rec = self._private.get(cm)
            if rec is None or rec["expires_at"] <= now:
                return None
            return {
                "commitment": rec["commitment"],
                "node_id": rec["node_id"],
                "hint": copy.deepcopy(rec["hint"]),
                "envelope": copy.deepcopy(rec["envelope"]),
                "seq": rec["seq"],
                "published_at": rec["published_at"],
                "expires_at": rec["expires_at"],
                "ttl_remaining_s": max(0, int(rec["expires_at"] - now)),
            }
def _check_bearer_auth(request, expected_token):
    if not expected_token:
        return True
    auth = str(request.headers.get("authorization") or "").strip()
    if not auth.lower().startswith("bearer "):
        return False
    supplied = auth[7:].strip()
    if not supplied:
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), expected_token.encode("utf-8"))
async def _parse_json_body(request):
    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        return None, (413, {"error": "payload_too_large"})
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None, (400, {"error": "invalid_json"})
    if not isinstance(data, dict):
        return None, (400, {"error": "invalid_envelope"})
    return data, None
def create_discovery_app(store=None, auth_token=None, rate_limit=120):
    directory = store if store is not None else DiscoveryStore()
    expected_token = str(auth_token or "").strip() or None
    limiter = _RateLimiter(limit=int(rate_limit), window_s=60.0) if (rate_limit is not None and int(rate_limit) > 0) else None
    app = FastAPI(title="SHARD Discovery")
    def _enforce_rate_limit(request):
        if limiter is None:
            return None
        client_ip = request.client.host if request.client else "unknown"
        ok, retry_after = limiter.allow(client_ip)
        if not ok:
            return JSONResponse(status_code=429, content={"error": "rate_limit_exceeded", "retry_after_s": retry_after}, headers={"Retry-After": str(retry_after)})
        return None
    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "service": "shard-discovery", "active_servers": directory.count(), "auth_required": bool(expected_token)}
    @app.get("/v1/discovery/servers")
    def list_servers_route(request: Request, q: str = "", model: str = "", region: str = "", access: str = "", online_only: bool = True, sort: str = "activity", limit: int = 100):
        rl = _enforce_rate_limit(request)
        if rl is not None:
            return rl
        servers = directory.list_servers(q=q, model=model, region=region, access=access, online_only=online_only, sort=sort, limit=limit)
        return {"status": "ok", "servers": servers, "count": len(servers), "server_time": directory._now()}
    @app.get("/v1/discovery/lookup/{node_id}")
    def lookup_route(request: Request, node_id: str):
        rl = _enforce_rate_limit(request)
        if rl is not None:
            return rl
        rec = directory.lookup(node_id)
        if rec is None:
            return JSONResponse(status_code=404, content={"error": "node_not_found"})
        return {"status": "ok", "server": rec}
    @app.post("/v1/discovery/register")
    async def register_route(request: Request):
        rl = _enforce_rate_limit(request)
        if rl is not None:
            return rl
        if not _check_bearer_auth(request, expected_token):
            return JSONResponse(status_code=403, content={"error": "forbidden"})
        body, err = await _parse_json_body(request)
        if err is not None:
            return JSONResponse(status_code=err[0], content=err[1])
        code, res = directory.register(body)
        return JSONResponse(status_code=code, content=res)
    @app.post("/v1/discovery/heartbeat")
    async def heartbeat_route(request: Request):
        rl = _enforce_rate_limit(request)
        if rl is not None:
            return rl
        if not _check_bearer_auth(request, expected_token):
            return JSONResponse(status_code=403, content={"error": "forbidden"})
        body, err = await _parse_json_body(request)
        if err is not None:
            return JSONResponse(status_code=err[0], content=err[1])
        code, res = directory.heartbeat(body)
        return JSONResponse(status_code=code, content=res)
    @app.post("/v1/discovery/unregister")
    async def unregister_route(request: Request):
        rl = _enforce_rate_limit(request)
        if rl is not None:
            return rl
        if not _check_bearer_auth(request, expected_token):
            return JSONResponse(status_code=403, content={"error": "forbidden"})
        body, err = await _parse_json_body(request)
        if err is not None:
            return JSONResponse(status_code=err[0], content=err[1])
        code, res = directory.unregister(body)
        return JSONResponse(status_code=code, content=res)
    @app.post("/v1/discovery/private/publish")
    async def private_publish_route(request: Request):
        rl = _enforce_rate_limit(request)
        if rl is not None:
            return rl
        if not _check_bearer_auth(request, expected_token):
            return JSONResponse(status_code=403, content={"error": "forbidden"})
        body, err = await _parse_json_body(request)
        if err is not None:
            return JSONResponse(status_code=err[0], content=err[1])
        code, res = directory.publish_private_commitment(body)
        return JSONResponse(status_code=code, content=res)
    @app.get("/v1/discovery/private/lookup/{commitment}")
    def private_lookup_route(request: Request, commitment: str):
        rl = _enforce_rate_limit(request)
        if rl is not None:
            return rl
        rec = directory.lookup_private_commitment(commitment)
        if rec is None:
            return JSONResponse(status_code=404, content={"error": "commitment_not_found"})
        return {"status": "ok", "commitment": rec["commitment"], "record": rec}
    return app
def serve_discovery(host="0.0.0.0", port=8090, auth_token=None, rate_limit=120, log_level="warning"):
    import uvicorn
    store = DiscoveryStore()
    app = create_discovery_app(store=store, auth_token=auth_token, rate_limit=rate_limit)
    uvicorn.run(app, host=host, port=int(port), log_level=log_level)
def _identity_node_id(identity):
    if isinstance(identity, dict):
        return str(identity.get("node_id") or "").strip()
    return str(getattr(identity, "node_id", "") or "").strip()
def _identity_public_key(identity):
    if isinstance(identity, dict):
        return identity.get("pubkey") or identity.get("pubkey_b64") or identity.get("public_key") or identity.get("public_key_b64") or ""
    return getattr(identity, "pubkey_b64", None) or getattr(identity, "pubkey", None) or getattr(identity, "public_key", None) or ""
class DiscoveryClient:
    def __init__(self, urls=None, offline_only=False, auth_tokens=None, auth_token=None, timeout_s=5.0, max_skew_s=60, session=None, clock=None):
        self.offline_only = bool(offline_only)
        self.timeout_s = max(0.5, float(timeout_s))
        self.max_skew_s = max(1, int(max_skew_s))
        self._session = session
        self._clock = clock
        self._lock = threading.Lock()
        self._seq_by_node = {}
        self.default_auth_token = str(auth_token or "").strip() or None
        self.auth_tokens = {}
        if isinstance(auth_tokens, dict):
            for k, v in auth_tokens.items():
                if v:
                    self.auth_tokens[str(k).strip().rstrip("/")] = str(v).strip()
        self.urls = []
        self.rejected_urls = []
        seen = set()
        for raw_u in (urls or []):
            s = str(raw_u or "").strip()
            if not s:
                continue
            ok, reason, info = net_identity.validate_external_service_url(s, kind="discovery")
            if not ok:
                self.rejected_urls.append({"url": s, "reason": str(reason or "invalid_url")})
                continue
            norm = s.rstrip("/")
            if isinstance(info, dict):
                cand = info.get("url") or info.get("normalized_url")
                if isinstance(cand, str) and cand.strip():
                    norm = cand.strip().rstrip("/")
            if norm not in seen:
                seen.add(norm)
                self.urls.append(norm)
                if s.rstrip("/") in self.auth_tokens and norm not in self.auth_tokens:
                    self.auth_tokens[norm] = self.auth_tokens[s.rstrip("/")]
    def _now(self):
        return float(self._clock() if self._clock is not None else time.time())
    def _next_seq(self, node_id):
        with self._lock:
            nxt = self._seq_by_node.get(node_id, 0) + 1
            self._seq_by_node[node_id] = nxt
            return nxt
    def _observe_seq(self, node_id, seq):
        if _is_int(seq) and node_id:
            with self._lock:
                if seq > self._seq_by_node.get(node_id, 0):
                    self._seq_by_node[node_id] = seq
    def _sign_envelope(self, identity, action, body_dict):
        node_id = _identity_node_id(identity)
        if not node_id:
            raise ValueError("missing_node_id")
        body = copy.deepcopy(body_dict) if isinstance(body_dict, dict) else {}
        if "nonce" not in body:
            body["nonce"] = secrets.token_hex(16)
        candidates = []
        for attr in ("sign_envelope", "build_envelope", "create_envelope"):
            fn = getattr(identity, attr, None)
            if callable(fn):
                candidates.append((fn, False))
        for attr in ("sign_envelope", "build_envelope", "create_envelope"):
            fn = getattr(net_identity, attr, None)
            if callable(fn):
                candidates.append((fn, True))
        for fn, is_mod in candidates:
            sig = inspect.signature(fn)
            params = sig.parameters
            kwargs = {}
            args = []
            if is_mod:
                if "identity" in params:
                    kwargs["identity"] = identity
                else:
                    args.append(identity)
            if "action" in params:
                kwargs["action"] = action
            else:
                args.append(action)
            if "body_dict" in params:
                kwargs["body_dict"] = body
            elif "body" in params:
                kwargs["body"] = body
            elif "payload" in params:
                kwargs["payload"] = body
            else:
                args.append(body)
            if "seq" in params:
                p_seq = params["seq"]
                has_internal_seq = hasattr(identity, "next_seq") or hasattr(identity, "disc_seq") or hasattr(identity, "seq")
                if p_seq.default is inspect.Parameter.empty or p_seq.default is not None or not has_internal_seq:
                    kwargs["seq"] = self._next_seq(node_id)
            env = fn(*args, **kwargs)
            if isinstance(env, dict):
                env_body = _extract_body(env)
                env_seq = _extract_seq(env, env_body if isinstance(env_body, dict) else {})
                if env_seq is not None:
                    self._observe_seq(node_id, env_seq)
                return env
        seq = self._next_seq(node_id)
        env = {
            "protocol": getattr(net_identity, "PROTOCOL_DISC_V1", "shard-disc-v1"),
            "action": action,
            "node_id": node_id,
            "pubkey": _identity_public_key(identity),
            "ts": round(self._now(), 3),
            "seq": seq,
            "body": body,
        }
        signer = getattr(identity, "sign", None)
        if callable(signer):
            canonical = json.dumps(env, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            env["sig"] = signer(canonical)
        return env
    def _headers_for(self, base_url, is_mutation=False):
        headers = {"Accept": "application/json"}
        if is_mutation:
            headers["Content-Type"] = "application/json"
            tok = self.auth_tokens.get(base_url) or self.default_auth_token
            if tok:
                headers["Authorization"] = f"Bearer {tok}"
        return headers
    def _http_request(self, method, base_url, path, params=None, json_body=None, is_mutation=False):
        target = f"{base_url}{path}"
        headers = self._headers_for(base_url, is_mutation=is_mutation)
        sess = self._session.get(base_url) if isinstance(self._session, dict) else self._session
        if sess is not None:
            fn = getattr(sess, method.lower())
            sig = inspect.signature(fn)
            kw = {"headers": headers}
            if type(sess).__name__ != "TestClient":
                kw["timeout"] = self.timeout_s
            if params is not None:
                kw["params"] = params
            if json_body is not None:
                kw["json"] = json_body
            if "allow_redirects" in sig.parameters:
                kw["allow_redirects"] = False
            elif "follow_redirects" in sig.parameters:
                kw["follow_redirects"] = False
            return fn(target, **kw)
        if method.upper() == "GET":
            return requests.get(target, params=params, headers=headers, timeout=self.timeout_s, allow_redirects=False)
        return requests.post(target, json=json_body, headers=headers, timeout=self.timeout_s, allow_redirects=False)
    def _broadcast_mutation(self, path, envelope):
        if self.offline_only:
            return {"status": "offline", "ok": False, "succeeded": 0, "failed": 0, "results": [], "rejected_urls": list(self.rejected_urls)}
        if not self.urls:
            return {"status": "no_endpoints", "ok": False, "succeeded": 0, "failed": 0, "results": [], "rejected_urls": list(self.rejected_urls)}
        results = []
        for url in self.urls:
            try:
                resp = self._http_request("POST", url, path, json_body=envelope, is_mutation=True)
                data = None
                try:
                    data = resp.json()
                except Exception:
                    data = None
                ok = 200 <= int(resp.status_code) < 300
                err = None if ok else ((data or {}).get("error") if isinstance(data, dict) else f"http_{resp.status_code}")
                results.append({"url": url, "ok": ok, "status_code": int(resp.status_code), "data": data, "error": err})
            except Exception as exc:
                results.append({"url": url, "ok": False, "status_code": None, "data": None, "error": f"{type(exc).__name__}:{exc}"})
        succeeded = sum(1 for r in results if r["ok"])
        failed = len(results) - succeeded
        overall_ok = succeeded > 0
        body = _extract_body(envelope)
        return {
            "status": "ok" if overall_ok else "error",
            "ok": overall_ok,
            "succeeded": succeeded,
            "failed": failed,
            "node_id": envelope.get("node_id"),
            "seq": _extract_seq(envelope, body if isinstance(body, dict) else {}),
            "results": results,
            "rejected_urls": list(self.rejected_urls),
        }
    def register_server(self, identity, listing_dict, ttl_s=60):
        if not isinstance(listing_dict, dict):
            return {"status": "error", "ok": False, "error": "invalid_listing", "succeeded": 0, "failed": 0, "results": []}
        raw_vis = listing_dict.get("visibility")
        if raw_vis is not None and str(raw_vis).strip().lower() != "public":
            return {"status": "rejected", "ok": False, "error": "private_listing_rejected", "succeeded": 0, "failed": 0, "results": []}
        if self.offline_only:
            return {"status": "offline", "ok": False, "succeeded": 0, "failed": 0, "results": [], "rejected_urls": list(self.rejected_urls)}
        if not self.urls:
            return {"status": "no_endpoints", "ok": False, "succeeded": 0, "failed": 0, "results": [], "rejected_urls": list(self.rejected_urls)}
        node_id = _identity_node_id(identity)
        raw_copy = copy.deepcopy(listing_dict)
        if not raw_copy.get("model") and raw_copy.get("active_model"):
            raw_copy["model"] = raw_copy["active_model"]
        acc = str(raw_copy.get("access_mode") or raw_copy.get("access") or "").strip().lower()
        if acc and "open" not in raw_copy and "requires_invite" not in raw_copy:
            if acc in ("invite", "invite_only", "invite-only"):
                raw_copy["open"] = False
                raw_copy["requires_invite"] = True
            elif acc == "open":
                raw_copy["open"] = True
                raw_copy["requires_invite"] = False
        sanitized = net_identity.sanitize_public_listing(raw_copy)
        if not isinstance(sanitized, dict):
            return {"status": "error", "ok": False, "error": "invalid_listing", "succeeded": 0, "failed": 0, "results": []}
        sanitized["visibility"] = "public"
        sanitized["node_id"] = node_id
        clamped_ttl = max(15, min(120, int(ttl_s or 60)))
        action = _action_for_domain(getattr(net_identity, "DOMAIN_DISC_REG", None), "register")
        envelope = self._sign_envelope(identity, action, {"listing": sanitized, "ttl_s": clamped_ttl})
        return self._broadcast_mutation("/v1/discovery/register", envelope)
    def heartbeat_server(self, identity, metrics=None, ttl_s=60, **kwargs):
        if self.offline_only:
            return {"status": "offline", "ok": False, "succeeded": 0, "failed": 0, "results": [], "rejected_urls": list(self.rejected_urls)}
        if not self.urls:
            return {"status": "no_endpoints", "ok": False, "succeeded": 0, "failed": 0, "results": [], "rejected_urls": list(self.rejected_urls)}
        merged = {}
        if isinstance(metrics, dict):
            merged.update(metrics)
        for key in ("active_model", "status", "slots_available", "load", "participant_count", "max_participants"):
            if key in kwargs and kwargs[key] is not None:
                merged[key] = kwargs[key]
        body = _sanitize_hb_metrics(merged)
        raw_ttl = kwargs.get("ttl_s", merged.get("ttl_s", ttl_s))
        body["ttl_s"] = max(15, min(120, int(raw_ttl or 60)))
        action = _action_for_domain(getattr(net_identity, "DOMAIN_DISC_HB", None), "heartbeat")
        envelope = self._sign_envelope(identity, action, body)
        return self._broadcast_mutation("/v1/discovery/heartbeat", envelope)
    def unregister_server(self, identity):
        if self.offline_only:
            return {"status": "offline", "ok": False, "succeeded": 0, "failed": 0, "results": [], "rejected_urls": list(self.rejected_urls)}
        if not self.urls:
            return {"status": "no_endpoints", "ok": False, "succeeded": 0, "failed": 0, "results": [], "rejected_urls": list(self.rejected_urls)}
        node_id = _identity_node_id(identity)
        action = _action_for_domain(getattr(net_identity, "DOMAIN_DISC_UNREG", None), "unregister")
        envelope = self._sign_envelope(identity, action, {"node_id": node_id})
        return self._broadcast_mutation("/v1/discovery/unregister", envelope)
    def _verify_server_entry(self, entry, now, expected_node_id=None, online_only=True):
        if not isinstance(entry, dict):
            return None
        env = entry.get("envelope")
        if not isinstance(env, dict):
            return None
        reg_node_id = str(env.get("node_id") or "").strip()
        if not reg_node_id:
            return None
        if expected_node_id and reg_node_id != expected_node_id:
            return None
        exp_at = entry.get("expires_at")
        if online_only and exp_at is not None:
            try:
                if float(exp_at) <= now:
                    return None
            except Exception:
                return None
        reg_body = _extract_body(env)
        if not isinstance(reg_body, dict):
            reg_body = {}
        reg_seq = _extract_seq(env, reg_body)
        if reg_seq is None:
            return None
        hb_env = entry.get("last_hb_envelope")
        hb_valid = False
        hb_metrics = {}
        eff_seq = reg_seq
        if hb_env is not None:
            if not isinstance(hb_env, dict):
                return None
            hb_expected = _resolve_expected_action(hb_env, getattr(net_identity, "DOMAIN_DISC_HB", None), "heartbeat")
            ok_hb, _ = net_identity.verify_envelope(hb_env, expected_action=hb_expected, max_skew_s=self.max_skew_s)
            if not ok_hb:
                return None
            if str(hb_env.get("node_id") or "").strip() != reg_node_id:
                return None
            hb_body = _extract_body(hb_env)
            if not isinstance(hb_body, dict):
                hb_body = {}
            hb_seq = _extract_seq(hb_env, hb_body)
            if hb_seq is None or hb_seq <= reg_seq:
                return None
            hb_metrics = _sanitize_hb_metrics(hb_body)
            eff_seq = hb_seq
            hb_valid = True
        reg_expected = _resolve_expected_action(env, getattr(net_identity, "DOMAIN_DISC_REG", None), "register")
        ok_reg, reg_reason = net_identity.verify_envelope(env, expected_action=reg_expected, max_skew_s=self.max_skew_s)
        if not ok_reg:
            reason_l = str(reg_reason or "").lower()
            if hb_valid and any(tok in reason_l for tok in ("skew", "stale", "expired", "ts", "time")):
                ok_reg_sig, _ = net_identity.verify_envelope(env, expected_action=reg_expected, max_skew_s=31536000)
                if not ok_reg_sig:
                    return None
            else:
                return None
        raw_listing, _ = _extract_listing_and_ttl(env, reg_body)
        if not isinstance(raw_listing, dict):
            return None
        if str(raw_listing.get("visibility") or "").strip().lower() != "public":
            return None
        l_nid = raw_listing.get("node_id")
        if l_nid is not None and str(l_nid).strip() and str(l_nid).strip() != reg_node_id:
            return None
        clean_listing = net_identity.sanitize_public_listing(copy.deepcopy(raw_listing))
        if not isinstance(clean_listing, dict):
            return None
        clean_listing["visibility"] = "public"
        clean_listing["node_id"] = reg_node_id
        clean_listing.update(hb_metrics)
        if online_only and str(clean_listing.get("status") or "online").strip().lower() == "offline":
            return None
        return {
            "node_id": reg_node_id,
            "listing": clean_listing,
            "envelope": copy.deepcopy(env),
            "last_hb_envelope": copy.deepcopy(hb_env) if hb_valid else None,
            "last_seq": eff_seq,
            "registered_at": entry.get("registered_at"),
            "updated_at": entry.get("updated_at") or env.get("ts") or now,
            "expires_at": exp_at,
        }
    def fetch_servers(self, q="", model="", region="", access="", sort="activity", limit=100, online_only=True):
        if self.offline_only:
            return {"status": "offline", "servers": [], "count": 0, "dropped": 0, "endpoints": [], "rejected_urls": list(self.rejected_urls)}
        if not self.urls:
            return {"status": "no_endpoints", "servers": [], "count": 0, "dropped": 0, "endpoints": [], "rejected_urls": list(self.rejected_urls)}
        now = self._now()
        lim = max(1, min(500, int(limit or 100)))
        params = {
            "q": q,
            "model": model,
            "region": region,
            "access": access,
            "sort": sort,
            "limit": lim,
            "online_only": "true" if online_only else "false",
        }
        dedup = {}
        endpoints = []
        total_dropped = 0
        any_ok = False
        for url in self.urls:
            try:
                resp = self._http_request("GET", url, "/v1/discovery/servers", params=params, is_mutation=False)
                if int(resp.status_code) != 200:
                    endpoints.append({"url": url, "ok": False, "status_code": int(resp.status_code), "server_count": 0, "dropped": 0, "error": f"http_{resp.status_code}"})
                    continue
                data = resp.json()
                raw_list = data if isinstance(data, list) else (data.get("servers") if isinstance(data, dict) else None)
                if not isinstance(raw_list, list):
                    endpoints.append({"url": url, "ok": False, "status_code": 200, "server_count": 0, "dropped": 0, "error": "invalid_response_format"})
                    continue
                any_ok = True
                ep_valid = 0
                ep_dropped = 0
                for raw_entry in raw_list:
                    verified = self._verify_server_entry(raw_entry, now, online_only=online_only)
                    if verified is None:
                        ep_dropped += 1
                        total_dropped += 1
                        continue
                    ep_valid += 1
                    nid = verified["node_id"]
                    prev = dedup.get(nid)
                    if prev is None or verified["last_seq"] > prev["last_seq"] or (verified["last_seq"] == prev["last_seq"] and float(verified.get("updated_at") or 0) > float(prev.get("updated_at") or 0)):
                        verified["source_url"] = url
                        dedup[nid] = verified
                endpoints.append({"url": url, "ok": True, "status_code": 200, "server_count": ep_valid, "dropped": ep_dropped, "error": None})
            except Exception as exc:
                endpoints.append({"url": url, "ok": False, "status_code": None, "server_count": 0, "dropped": 0, "error": f"{type(exc).__name__}:{exc}"})
        servers = [rec for rec in dedup.values() if _listing_matches(rec, q=q, model=model, region=region, access=access)]
        servers.sort(key=lambda r: _sort_key_for_server(r, sort))
        servers = servers[:lim]
        return {
            "status": "ok" if any_ok else "error",
            "servers": servers,
            "count": len(servers),
            "dropped": total_dropped,
            "endpoints": endpoints,
            "rejected_urls": list(self.rejected_urls),
        }
    def lookup_node(self, node_id):
        nid = str(node_id or "").strip()
        if not nid or self.offline_only or not self.urls:
            return None
        now = self._now()
        best = None
        safe_nid = quote(nid, safe="")
        for url in self.urls:
            try:
                resp = self._http_request("GET", url, f"/v1/discovery/lookup/{safe_nid}", is_mutation=False)
                if int(resp.status_code) != 200:
                    continue
                data = resp.json()
                raw_entry = data.get("server") if isinstance(data, dict) and isinstance(data.get("server"), dict) else data
                verified = self._verify_server_entry(raw_entry, now, expected_node_id=nid, online_only=True)
                if verified is None:
                    continue
                if best is None or verified["last_seq"] > best["last_seq"]:
                    verified["source_url"] = url
                    best = verified
            except Exception:
                continue
        return best
    def publish_private_commitment(self, identity, commitment, hint, ttl_s=60):
        cm = str(commitment or "").strip().lower()
        if not _is_sha256_hex(cm):
            return {"status": "error", "ok": False, "error": "invalid_commitment", "succeeded": 0, "failed": 0, "results": []}
        clean_hint = _sanitize_private_hint(hint)
        if clean_hint is None:
            return {"status": "error", "ok": False, "error": "invalid_rendezvous_hint", "succeeded": 0, "failed": 0, "results": []}
        if self.offline_only:
            return {"status": "offline", "ok": False, "succeeded": 0, "failed": 0, "results": [], "rejected_urls": list(self.rejected_urls)}
        if not self.urls:
            return {"status": "no_endpoints", "ok": False, "succeeded": 0, "failed": 0, "results": [], "rejected_urls": list(self.rejected_urls)}
        clamped_ttl = max(1, min(120, int(ttl_s or 60)))
        action = _action_for_domain(getattr(net_identity, "DOMAIN_RELAY_REG", None), "relay_register")
        envelope = self._sign_envelope(identity, action, {"commitment": cm, "hint": clean_hint, "ttl_s": clamped_ttl})
        res = self._broadcast_mutation("/v1/discovery/private/publish", envelope)
        res["commitment"] = cm
        return res
    def lookup_private_commitment(self, commitment, expected_node_id=None):
        cm = str(commitment or "").strip().lower()
        if not _is_sha256_hex(cm) or self.offline_only or not self.urls:
            return None
        now = self._now()
        best = None
        for url in self.urls:
            try:
                resp = self._http_request("GET", url, f"/v1/discovery/private/lookup/{cm}", is_mutation=False)
                if int(resp.status_code) != 200:
                    continue
                data = resp.json()
                rec = data.get("record") if isinstance(data, dict) and isinstance(data.get("record"), dict) else data
                if not isinstance(rec, dict):
                    continue
                env = rec.get("envelope")
                if not isinstance(env, dict):
                    continue
                expected = _resolve_private_expected_action(env)
                ok, _ = net_identity.verify_envelope(env, expected_action=expected, max_skew_s=max(self.max_skew_s, 120))
                if not ok:
                    continue
                env_nid = str(env.get("node_id") or "").strip()
                if not env_nid or (expected_node_id and env_nid != expected_node_id):
                    continue
                body = _extract_body(env)
                if not isinstance(body, dict) or "listing" in body:
                    continue
                if str(body.get("commitment") or "").strip().lower() != cm:
                    continue
                ttl_val = min(120, max(1, int(body.get("ttl_s") or 60)))
                env_ts = env.get("ts")
                if isinstance(env_ts, (int, float)) and float(env_ts) + ttl_val + self.max_skew_s < now:
                    continue
                exp_at = rec.get("expires_at")
                if exp_at is not None and float(exp_at) <= now:
                    continue
                seq = _extract_seq(env, body)
                if seq is None:
                    continue
                raw_hint = body.get("hint") if body.get("hint") is not None else (body.get("rendezvous") if body.get("rendezvous") is not None else body.get("ciphertext"))
                clean_hint = _sanitize_private_hint(raw_hint)
                if clean_hint is None:
                    continue
                verified = {
                    "commitment": cm,
                    "node_id": env_nid,
                    "hint": clean_hint,
                    "envelope": copy.deepcopy(env),
                    "seq": seq,
                    "expires_at": exp_at,
                    "source_url": url,
                }
                if best is None or verified["seq"] > best["seq"]:
                    best = verified
            except Exception:
                continue
        return best
