# LAN Sharding — as built (v1.7.0, v1.8.0)

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

- **UDP beacon**: port `8001`, JSON announce every 2 s, peer TTL 8 s. Since v1.8.0 the listener is a permanent daemon thread started in `LanManager.__init__` (it survives `mode: off` and retries the bind once per second if the port is taken, e.g. by a second shard instance on the same PC — both bind with `SO_REUSEADDR` and filter their own beacons by name). Peers never auto-join; joining is always an explicit user action.
- **Beacon payload** (v1.8.0): `{app, ver, name, role, model, port, open, ts}` — `model` is the server's currently loaded model (lazy engine lookup inside the announce thread; engine imports lan at module level, so it must never be imported at lan module top level), `open` marks an open network.
- **Pairing code** (closed networks): `XXXX-XXXX` from a 31-char alphabet (no `0/O/1/I/L`); shown on the master's dashboard/CLI, typed on the joining machine. `handle_pair` is idempotent per IP (re-pair reuses the issued token).
- **Open networks** (v1.8.0): the master sets the flag at create time (`create(name, open_net=True)`) or later (`set_open(open)`); it announces `open: true` in the beacon and `handle_pair` skips code validation entirely. Joining such a network sends an empty code. The toggle is server-only (off → error «Сеть не создана — открыть можно только свою сеть»), and `off()` clears the flag together with the role. A joining machine can see the open/closed state from the beacon and the status endpoint.
- **Member token**: `sk-shard-lan-` + 16 hex chars, issued by the master at pair time. Heartbeats and leave requests carry it; the master matches **both IP and token**. `status()` never exposes member tokens (verified by test).
- **Auth gate**: a FastAPI HTTP middleware on paths starting `/v1` — when the machine is a LAN server and the client is not loopback, a member token is required. Loopback stays open (the owner's own dashboard/CLI). **`/api/*` (dashboard + LAN management) stays trusted-LAN**: an MVP limitation, documented honestly — anything that can reach the master's port can manage the network. Open networks deliberately widen that surface on the *join* side — they only make sense on a network you trust.

## 4. Memory math (all values verified empirically)

Everything lives in `lan.py`:

- `device_budget(vram_free_mb, ram_free_mb)` — same hard reserves as the single-machine advisor (`fit.py`: `VRAM_RESERVE_MB=400`, `RAM_RESERVE_MB=2048`): `device_budget(4096,0) → (3696,0)`, `device_budget(0,16077) → (0,14029)`.
- `split_weights(budgets)` — proportional `-ts` weights; all-zero → `[1,1]` honest equal fallback.
- `rpc_targets()` — samples the **master's hardware live per call** (free VRAM/RAM *before* weights are pushed into the pool), appends each alive worker's `device_budget` from its last heartbeat `hw`, returns `{"servers": "ip:port,...", "split": "N0,N1,..."}`. Verified live: master VRAM 1885 → share 1485 (1885−400), worker RAM 9840 → share 7792 (9840−2048) → `-ts 1485,7792`.
- `pool_capacity_mb()` — usable pooled memory: per device `vram_budget if >0 else ram_budget`, summed.

Fit integration (`engine.py`): the single-machine refusal is bypassed **only** when pooling is active *and* `fit.get("disk_ok") is not False`; the pool fit check compares `pool_capacity_mb()` against the advisor's `recommended` quant `total_gb`. `ngl` is forced to 999 (weights are pre-split across devices). A pooled-but-not-fitting load still refuses, with the note "Модель не влезает даже в объединённую память shard-сети".

## 5. User surface

- **Dashboard**: "Сеть" tab — create a network (name optional, «Открытая сеть» checkbox), join with IP + optional code (+ "стать вычислительным узлом"), status box with mode badge, open/closed badge and an «Открыть/Закрыть сеть» toggle (server only), pairing code (click to copy), live member list (green/red dots), a reload-model hint when a compute worker joins after load, the **«Серверы рядом» list** — nearby masters only (`role === 'server'`) with name, IP:port, open badge («открытая» / «нужен код»), loaded model, and the green **«Войти на сервер»** button on open networks — and the live `--rpc`/`-ts` values.
- **One-click join** (`lanJoinQuick`): POSTs `/api/lan/join {ip, code: '', share: true}` — the joining machine shares its VRAM/RAM by default, exactly the "другой чел увидел сервер и нажал Войти" flow. If the RPC runtime is missing on that machine, the join still succeeds in plain member mode (dashboard toast and CLI print the warning).
- **CLI** (`shard lan`): `status | create [name] [--open] | join <ip> [code] [--share] | off` — thin HTTP proxy to `{BASE_URL}/api/lan/*` (`SHARD_BASE_URL` env or `http://localhost:8000`); RU output; server offline → "Shard Server не запущен"; status shows the nearby server list with a `shard lan join <ip>` hint.
- **Kill switch**: `shard lan off` / dashboard button — drops every role, stops the RPC process and threads, notifies the master. Also the auto-off path when the master dies. The nearby server list survives `off()` (peers are kept so you can re-join without waiting for a new beacon).

## 6. Known assumptions & MVP limits (honest)

- **`-ts` device order** follows documented llama.cpp behavior (local device first, then `--rpc` order) — verified against docs, not yet against live weight placement; live check planned for Phase 1.1.
- **`/api/*` is trusted-LAN** (see §3) — protect the port at the network layer for now. Open networks lower the join barrier deliberately: anyone who reaches the master can become a worker/member without a code — enable only on trusted networks (home LAN / your Radmin VPN room), never on hotel/office Wi-Fi.
- **Discovery reach**: the UDP beacon works on the same subnet and usually in Radmin VPN / ZeroTier rooms; **Tailscale has no L2 broadcast**, so its users join by manual IP — that path always works. No NAT traversal in MVP (strangers-over-internet needs a hosted registry + real auth, out of scope).
- **Model file location**: the master downloads the GGUF once; workers only need the RPC binary, not the weights.
- **Weights distribution timing**: `rpc_targets()` is consumed only when the master loads the model (engine reads it at load time). A worker that joined after load does not hold weights until the master **reloads the model** — the dashboard shows a yellow hint when this happens.
- Heartbeats are the only liveness signal — a hung-but-alive RPC process is not detected (llama-server fails visibly on RPC loss).

## 7. Tests

30 LAN tests in `tests_shard.py` (80 total, all green) cover the pure math (budgets, splits, formats), the pairing/heartbeat state machine, token leak prevention (`status()` strips member tokens), the live-sampled pooled split, and the v1.8.0 surface — open-flag beacon payload, open pairing with empty and wrong codes, closed-network rejection of empty codes, the open toggle (and its server-only guard), join-without-IP guard, mocked open join → worker mode, runtime-missing fallback → member mode with warning, and the 403 «Сеть закрыта: нужен код подключения» message — every expected value verified in scratch runs before being written down.
