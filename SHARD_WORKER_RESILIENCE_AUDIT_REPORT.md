# SHARD — Independent Worker Failure Resilience Audit Report

**Audit Date:** 2026-11-23  
**Auditor Role:** Independent Senior Distributed Systems Auditor & Reliability Engineer  
**Environment:** Windows 11 x64, Python 3.14.4, PyTorch 2.14.1+cpu, Transformers 5.20.2, FastAPI 0.141.1, httpx 0.28.1, requests 2.34.2, local `llama-server` / `ggml-rpc-server`  
**Execution Scope:** Localhost (`127.0.0.1`) multi-process OS workers, loopback TCP `SHPT` servers, scripted blackhole/trickle/stale peers, local `ThreadingHTTPServer` GGUF SSE mocks, FastAPI `TestClient`, and isolated Git worktree baseline verification. No remote CI workflow and no separate physical machines were executed during this audit.

---

## 1. Actual Git Branch & Commit Information

Independent inspection of the local repository at audit start revealed material discrepancies between the previous report's branch/commit state and actual Git refs:

| Git Attribute | Observed Value at Audit Start | Notes |
| :--- | :--- | :--- |
| Initially checked-out branch | `master` | Not `feat/worker-failure-resilience`. Switched to `feat/worker-failure-resilience` before applying audit fixes so `master` was left untouched. |
| Current `HEAD` commit | `04495bc1efd9c4daf886b98122c056a83312f076` | `docs(resilience): update README.md (3/3) [292/292]` |
| `feat/worker-failure-resilience` | `04495bc1efd9c4daf886b98122c056a83312f076` | Identical to `master`. |
| `origin/feat/worker-failure-resilience` | `04495bc1efd9c4daf886b98122c056a83312f076` | Already pushed to GitHub (`https://github.com/avelone123123/shard.git`). |
| `origin/master` | `04495bc1efd9c4daf886b98122c056a83312f076` | **Important:** The 292-commit series had already been fast-forwarded onto both `master` and `origin/master` before this audit began. |
| Working tree status at start | Clean (`0` uncommitted changes) | No existing work was overwritten or discarded. |
| Reported commit `42d01ed` | Exists locally (`42d01edba33e3300eb8697e1175ffe7e08d603a1`) | **Dangling/orphaned commit** (`git branch -a --contains 42d01ed` returned empty). Its tree SHA (`89def0c17b0a692a337f6981e36adb1de897f69d`) is byte-for-byte identical to `04495bc` (`git diff 42d01ed 04495bc` was empty). Commit `42d01ed` was subsequently split into 292 micro-commits (`f246e23`..`04495bc`) on top of `ff1155c`. |
| Actual base commit | `ff1155cd4d55d57a196db3b23c745d7d9be5ec36` | `feat/autonomous-shard-network` (`docs(network): update README.md (8/8) [251/251]`). `git merge-base 42d01ed 04495bc` = `ff1155c`. |
| Diff (`ff1155c..04495bc`) | 13 files changed, `+2409 / -274` lines | Matches `git show --stat 42d01ed` exactly. |
| Existing worktrees | `swarm-autonet-67ed27f7` at `7fad0df` | Preserved untouched. Temporary worktree created for Section 6 baseline verification was cleanly removed after comparison. |

---

## 2. Independent Assessment of Every Major Implementation Claim

Each major claim in `SHARD_WORKER_FAILURE_RESILIENCE_REPORT.md` was verified against source code and live execution. Multiple hidden defects were reproduced on unmodified commit `04495bc` and subsequently fixed on `feat/worker-failure-resilience`.

| Capability / Claim | Pre-Audit Finding on `04495bc` | Post-Fix Audit Status |
| :--- | :--- | :--- |
| Per-phase timeouts (`connect`, `load`, `prefill`, `decode`, `release`, `stall`, `request_deadline`) | Partially flawed: `connect()` ignored `request_deadline_s`, `send_fn()` used full step timeout without cancellation polling, `recv_exact_poll()` never polled on slow-trickling peers, `stop_cluster()` could not abort `load_stage()` (`self.runners` empty during load), GGUF keepalive comments defeated `stall_timeout_s`. | **VERIFIED** (all fixed and regression-tested) |
| 4-state worker liveness FSM (`HEALTHY`/`SUSPECT`/`FAILED`/`RECOVERING`) & probation | **Defective on `04495bc`:** (1) Single stale heartbeat revived a dead worker to `HEALTHY` if `probation_s <= suspect_after_s` without TCP verification; (2) `GET /api/cluster/plan` wiped `assigned_stage` on `SUSPECT` workers, blinding failure detection; (3) `probe_node_capabilities()` / `measure_tcp_link()` / `slots_status()` ran while holding `CLUSTER._lock`, starving heartbeats for 0.8s–1.9s and falsely failing healthy workers under tight intervals. | **VERIFIED** (all fixed and regression-tested) |
| `torch_pipeline_parallel` mid-generation recovery (`ResumeState`) | Worked for single-retry greedy in-process stop; **defective on `04495bc`** for: (1) multi-retry lock-wait / `not is_running()` failures erasing `ResumeState`; (2) `stage_idx` vs `stage_index` key mismatch leaving `stage=None` on real planned clusters; (3) `top_p`/`top_k`/`repetition_penalty` not forwarded; (4) `resumed_after_failure=True` on Case B restart. | **VERIFIED WITH LIMITATIONS** (context replay works deterministically for greedy/`top_k=1`; stochastic RNG not checkpointed; HF remote revision not SHA-pinned) |
| GGUF RPC mid-generation failure handling | Worked for mock stall/truncation; **defective on `04495bc`** because `_probe_assigned_workers()` ran after `if not can_restart: raise`, so failures after token 1 never probed dead RPC workers, and `_gguf_stream` masked `WORKER_LOST` aborts as generic `BACKEND_ERROR`. | **VERIFIED WITH LIMITATIONS** (fixed probing & abort precedence; mid-generation resume after token 1 remains unsupported by design on `llama-server`) |
| OpenAI & Anthropic streaming error contracts | **Defective on `04495bc`:** (1) `PipelineCoordinator.generate_stream()` yielded `{"type": "done"}` on `RequestCancelled`; (2) `/v1/messages` emitted `stop_reason: "end_turn"` on cancellation; (3) non-streaming sync APIs returned HTTP `200 OK` with `finish_reason: "stop"` on cancellation; (4) pre-header degraded cluster returned HTTP `200` + `message_start` instead of HTTP `503`. | **VERIFIED WITH LIMITATIONS** (all defects fixed and verified with strict SSE parsers; official `openai`/`anthropic` SDK packages are not installed in this environment) |
| Stale frame & execution ID rejection | **Defective on `04495bc`:** `out_meta.get(...) is not None` guards allowed responses omitting `request_id`, `seq`, or `execution_id` to pass unchecked; worker defaulted empty `request_id` to `"default"`. | **VERIFIED** (exact match required; missing fields rejected as `STALE_RESPONSE`) |
| Policy validation, thread-safety & log sanitization | **Defective on `04495bc`:** `{"decode_step_timeout_s": "nan"}` crashed Starlette (HTTP 500) and permanently poisoned `POLICY`; list body crashed with `AttributeError`; `monitor_tick_s=0` created a CPU busy loop; `configure()` partially mutated on error; `as_dict()` lacked lock; `METRICS.event()` leaked `user_prompt`, `auth_token`, `pairing_code`, and bearer/token substrings. | **VERIFIED** (bounded finite validation, atomic updates, locked reads, recursive log redaction) |
| Epoch & shutdown concurrency (`_cluster_epoch`, `_deploy_lock`) | **Defective on `04495bc`:** `stop_cluster()` could not abort `PipelineCoordinator.start()` during `load_stage()`; `ctx.workers` retained dead worker during retry backoff sleep; `_reform_cluster()` `finally` cleared `_reform_active` without epoch guard; `_inflight` keyed only by `request_id`. | **VERIFIED** (all races fixed and stress-tested) |
| Pre-existing classification of `tests_phase_i_product_hardening.py` `test_06` & `test_09` | Claimed pre-existing without baseline proof. | **VERIFIED** (independently proven identical on isolated base worktree `ff1155c`) |
| CI verification on GitHub Actions | Explicitly not run in previous report. | **NOT TESTED** (no remote CI executed) |
| Multi-machine physical WAN/LAN failure testing | Explicitly not run in previous report. | **NOT TESTED** (localhost loopback & local OS subprocesses only) |

---

## 3. Recovery Mechanism Details (`ResumeState` & `torch_pipeline_parallel`)

### Exact Mechanism Classification

SHARD's recovery mechanism in `shard_engine/distributed/pipeline_coordinator.py` and `shard_engine/distributed/cluster.py` is **KV-cache reconstruction through full-context prefill replay** — it is **NOT** real binary KV-cache restoration (no K/V attention tensors are serialized, transferred, or restored across workers).

Specifically, on recovery after `k >= 1` tokens were generated:
1. `PipelineCoordinator.generate_stream()` records `ResumeState(generated_ids, delivered_text)` on the raised `InferenceFailure`, releases KV caches on surviving stages (`release_request(rid, skip_workers=[failed_worker])`), and releases `_gen_lock`.
2. `ClusterManager._reform_cluster()` recomputes a partition plan across surviving `HEALTHY` nodes + Coordinator (when feasible) and deploys a new `PipelineCoordinator` with fresh `PipelineStageModule` instances.
3. `ClusterManager.generate_stream()` calls the new `PipelineCoordinator.generate_stream(..., resume=ResumeState)`.
4. Inside `generate_stream()`, `cur_ids = torch.cat([input_ids, torch.tensor([generated_ids], dtype=torch.long)], dim=-1)` is passed through a single `phase="prefill"` forward pass (`use_cache=True`) across all stages, allocating fresh `DynamicCache` objects populated for positions `0 .. len(prompt_ids) + len(generated_ids) - 1`.
5. `prev_text` is initialized to `resume.delivered_text`, `next_id` is sampled from `logits[0, -1, :]`, and `delta = full_txt[len(prev_text):]` yields only newly generated suffix text for the remaining `max_new - len(generated_ids)` steps.
