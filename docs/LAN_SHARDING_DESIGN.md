# LAN Sharding — as built (v1.7.0)

> **Status: Phase 1 shipped in v1.7.0.** Several machines on a LAN act as one big server for one model: memory is pooled with llama.cpp RPC, so large models fit even without one powerful GPU. Internet networks work through Radmin VPN / ZeroTier / Tailscale — no extra code, those tools give every PC a LAN-like IP. NAT traversal is not in the MVP.

## 1. What it is

Two mid-range PCs (e.g. 8 GB VRAM each) cannot each run a 32B model alone — but pooled memory can hold a 32B Q2_K (~12.2 GB). The fit advisor already refuses such models on one machine (`IMPOSSIBLE`); the LAN pool re-runs the same math with every device's budget summed.

The implementation reuses **llama.cpp's own RPC** (`ggml-rpc-server` + `--rpc` + `-ts`) instead of a custom forward protocol:

- **No custom wire protocol to write, test, and break** — llama.cpp handles weight distribution, per-layer compute, KV-cache sharding and all token traffic inside its existing, mature RPC path.
- **`-ts` (tensor split) assigns a proportional memory share to every device** — exactly the "pooled memory" model, and each device's share must fit there.
- Layer sharding (one activation per hop) stays the right choice over tensor all-reduce: per-token all-reduce needs NVLink-class bandwidth; a hidden-state hop is ~8 KB — trivial even on Wi-Fi 5. llama.cpp RPC implements this internally.

## 2. Roles

```
            LAN (Ethernet preferred; Wi-Fi works)
+------------------------+    +------------------------+   +------------------------+
|  MASTER (shard server) |    |  WORKER                |   |  MEMBER (client)       |
|                        |    |                        |   |                        |
|  llama-server          |    |  ggml-rpc-server.exe   |   |  only consumes /v1     |
|  --rpc ip:port,ip:port |<---|  --host 0.0.0.0        |   |  over HTTP             |
|  -ts N0,N1,...         |    |  --port 50052          |   |  (no compute shared)   |
|  owns model + API      |    |  heartbeats every 1 s  |   |                        |
+------------------------+    +------------------------+   +------------------------+
```

- **Master (server)** — runs the shard server: the public API, dashboard, the fit advisor, and `llama-server` with `--rpc <workers>` + `-ts <weights>`. It runs `ggml-rpc-server` implicitly through llama-server (the master is device 0 in the split).
- **Worker** — a compute member: spawns `ggml-rpc-server.exe --host 0.0.0.0 --port P` (bundled with the llama.cpp runtime shard already downloads to `~/.shard/runtime`) and heartbeats every 1 s. 3 consecutive missed heartbeats → dropped automatically.
- **Member** — client-only: consumes the master's model over HTTP, shares nothing.

## 3. Discovery, pairing, auth

- **UDP beacon**: port `8001`, JSON announce every 2 s, peer TTL 8 s. Peers are shown in the dashboard ("рядом в сети") but never auto-join.
- **Pairing code**: `XXXX-XXXX` from a 31-char alphabet (no `0/O/1/I/L`); shown on the master's dashboard/CLI, typed on the joining machine. `handle_pair` is idempotent per IP (re-pair reuses the issued token).
- **Member token**: `sk-shard-lan-` + 16 hex chars, issued by the master at pair time. Heartbeats and leave requests carry it; the master matches **both IP and token**. `status()` never exposes member tokens (verified by test).
- **Auth gate**: a FastAPI HTTP middleware on paths starting `/v1` — when the machine is a LAN server and the client is not loopback, a member token is required. Loopback stays open (the owner's own dashboard/CLI). **`/api/*` (dashboard + LAN management) stays trusted-LAN**: an MVP limitation, documented honestly — anything that can reach the master's port can manage the network.

## 4. Memory math (all values verified empirically)

Everything lives in `lan.py`:

- `device_budget(vram_free_mb, ram_free_mb)` — same hard reserves as the single-machine advisor (`fit.py`: `VRAM_RESERVE_MB=400`, `RAM_RESERVE_MB=2048`): `device_budget(4096,0) → (3696,0)`, `device_budget(0,16077) → (0,14029)`.
- `split_weights(budgets)` — proportional `-ts` weights; all-zero → `[1,1]` honest equal fallback.
- `rpc_targets()` — samples the **master's hardware live per call** (free VRAM/RAM *before* weights are pushed into the pool), appends each alive worker's `device_budget` from its last heartbeat `hw`, returns `{"servers": "ip:port,...", "split": "N0,N1,..."}`. Verified live: master VRAM 1885 → share 1485 (1885−400), worker RAM 9840 → share 7792 (9840−2048) → `-ts 1485,7792`.
- `pool_capacity_mb()` — usable pooled memory: per device `vram_budget if >0 else ram_budget`, summed.

Fit integration (`engine.py`): the single-machine refusal is bypassed **only** when pooling is active *and* `fit.get("disk_ok") is not False`; the pool fit check compares `pool_capacity_mb()` against the advisor's `recommended` quant `total_gb`. `ngl` is forced to 999 (weights are pre-split across devices). A pooled-but-not-fitting load still refuses, with the note "Модель не влезает даже в объединённую память shard-сети".

## 5. User surface

- **Dashboard**: "Сеть" tab — create a network (name optional), join with IP + code (+ "стать вычислительным узлом"), status box with mode badge, pairing code (click to copy), live member list (green/red dots), nearby peers, and the live `--rpc`/`-ts` values.
- **CLI** (`shard lan`): `status | create | join | off` — thin HTTP proxy to `{BASE_URL}/api/lan/*` (`SHARD_BASE_URL` env or `http://localhost:8000`); RU output; server offline → "Shard Server не запущен".
- **Kill switch**: `shard lan off` / dashboard button — drops every role, stops the RPC process and threads, notifies the master. Also the auto-off path when the master dies.

## 6. Known assumptions & MVP limits (honest)

- **`-ts` device order** follows documented llama.cpp behavior (local device first, then `--rpc` order) — verified against docs, not yet against live weight placement; live check planned for Phase 1.1.
- **`/api/*` is trusted-LAN** (see §3) — protect the port at the network layer for now.
- **Model file location**: the master downloads the GGUF once; workers only need the RPC binary, not the weights.
- No NAT traversal: for internet use, run the machines in a Radmin VPN / ZeroTier / Tailscale network and use those IPs.
- Heartbeats are the only liveness signal — a hung-but-alive RPC process is not detected (llama-server fails visibly on RPC loss).

## 7. Tests

19 LAN tests in `tests_shard.py` (69 total, all green) cover the pure math (budgets, splits, formats), the pairing/heartbeat state machine, token leak prevention (`status()` strips member tokens), and the live-sampled pooled split — every expected value verified in scratch runs before being written down.
