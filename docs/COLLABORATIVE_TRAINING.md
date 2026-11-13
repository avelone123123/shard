# Collaborative AI Training Ecosystem (SHARD x SOUP)

SHARD pairs distributed local LLM serving with an optional local fine-tuning workflow powered by [SOUP](https://github.com/MakazhanAlpamys/Soup):

```text
CREATE -> SHARE -> CONNECT -> USE -> FEEDBACK -> TRAIN -> IMPROVE -> DEPLOY (with Rollback)
```

SHARD remains a standalone inference runtime and LAN memory pool. SOUP is an optional integration: SHARD serves models, collects consent-gated user feedback, redacts sensitive data, prepares datasets, orchestrates SOUP training jobs, tracks evaluation metrics, deploys local fine-tuned checkpoints, and rolls back if needed.

---

## Architecture & Module Boundaries

| Component | Module | Responsibility |
| --- | --- | --- |
| SOUP Adapter | `shard_engine/soup_adapter.py` | Detects local `soup-cli`, validates Python `>=3.11,<3.14` compatibility, generates `soup.yaml`, runs `soup data validate`, `soup train`, `soup eval custom`, `soup merge`, `soup export` |
| Consent & Feedback Pipeline | `shard_engine/feedback.py` | Stores explicit feedback separately from runtime chats, masks secrets & PII before persistence, handles opt-out/revocation, deduplicates, exports `chatml` (SFT) & `dpo` snapshots |
| Training & Model Registry | `shard_engine/training.py` | Owner-initiated job lifecycle, VRAM conflict guard, cancellation/resume, model versioning, Baseline vs Candidate eval gate, approval gate, deployment & rollback |
| Inference Deployment | `shard_engine/engine.py` | Supports local merged HF directories, PEFT LoRA adapters, and local `.gguf` files seamlessly on deploy/rollback |
| Security & REST API | `shard_engine/server.py` | Enforces owner authorization on `/api/training/*` (loopback or `X-Shard-Owner-Token`) while letting consented participants submit feedback via `/api/feedback/*` |

---

## Verified Capabilities vs. Honest Boundaries

- **Single-node local training**: SOUP training jobs run on the SHARD host GPU/CPU runtime. SHARD's `ggml-rpc` LAN memory pool is for **inference only** (`llama.cpp`); multi-node distributed training over LAN is not active in v1.
- **Python version boundary**: Upstream `soup-cli` declares `requires-python = ">=3.11,<3.14"`. If you run SHARD on Python 3.14+, install SOUP in a separate Python 3.12 virtual environment and point SHARD to it:

```bash
python3.12 -m venv ~/.soup-venv
~/.soup-venv/bin/pip install "soup-cli[train]"
export SHARD_SOUP_PYTHON=~/.soup-venv/bin/python
```

Or on Windows PowerShell:

```powershell
py -3.12 -m venv "$HOME\.soup-venv"
& "$HOME\.soup-venv\Scripts\pip.exe" install "soup-cli[train]"
$env:SHARD_SOUP_PYTHON = "$HOME\.soup-venv\Scripts\python.exe"
```

On Python 3.11–3.13 you can install optional training extras directly:

```bash
pip install "shard-engine[training]"
```

---

## Privacy, Consent & Security Model

1. **No silent conversation capture**: Regular `/v1/chat/completions`, `/v1/messages`, and Chat Playground turns are never written to training datasets.
2. **Explicit per-submission consent**: `require_explicit_consent` is enabled by default (`consent_given: true` required).
3. **Remote contributions disabled by default**: `allow_remote_feedback` defaults to `false`. Even when enabled by the owner, connected LAN clients authenticate with their LAN token and cannot view other submissions, trigger training, read datasets, or deploy models (`HTTP 403 Forbidden`).
4. **Automatic redaction before disk write**: API keys (`sk-*`), AWS keys, GitHub tokens, JWTs, PEM private keys, bearer tokens, emails, and phone numbers are redacted before saving.
5. **Manual owner moderation**: Submissions land in `pending` review queue by default (`require_manual_review: true`).
6. **Participant revocation**: Users can inspect their pseudonymous submission IDs (`/api/feedback/my`) and revoke consent (`/api/feedback/revoke`), wiping their entries before training.
7. **Strict SFT vs DPO rules**: Thumbs-down ratings without a better correction never enter SFT datasets and never fabricate synthetic DPO preference pairs. DPO snapshots require explicit `prompt`, `chosen`, and distinct `rejected` pairs.
8. **Host owner authorization**: Remote requests to `/api/training/*` require `X-Shard-Owner-Token` stored locally in `<config_dir>/owner_token.txt`. Forwarded proxy headers without the token are rejected.

---

## End-to-End CLI Workflow (`shard training`)

```bash
# 1. Inspect SOUP readiness and training status
shard training doctor
shard training status

# 2. Configure privacy policy (optional)
shard training privacy --allow-remote on --require-review on --redact-secrets on

# 3. Review or add consented feedback, then build an immutable snapshot
shard training feedback --status pending
shard training feedback approve <entry_id>
shard training dataset build --task sft --name coding-v1

# 4. Validate config (--dry-run) and launch SOUP training
shard training start --dataset <snapshot_id> --base Qwen/Qwen2.5-Coder-1.5B-Instruct --dry-run
shard training start --dataset <snapshot_id> --base Qwen/Qwen2.5-Coder-1.5B-Instruct --epochs 3

# 5. Track logs, evaluate against base model, merge/export, approve & deploy
shard training logs <job_id>
shard training models
shard training eval <version_id>
shard training prepare <version_id> --target gguf --quant q4_k_m
shard training approve <version_id>
shard training deploy <version_id>

# 6. Instant rollback if needed
shard training rollback
```

---

## Dashboard UI

Open the SHARD web dashboard (`shard start`) and switch to:
- **Chat (`Чат`)**: Optional "Help improve this model" bar below assistant replies with explicit consent checkbox, optional improved answer box, and "My Contributions / Revoke" modal.
- **Training (`Обучение (SOUP)`)** *(owner-only)*: Five sub-tabs covering Overview & Audit Log, Feedback Queue & Dataset Builder, Training Launcher & Live Logs, Model Versions (Eval diff, Merge/GGUF prep, Approve/Deploy, 1-Click Rollback), and Privacy & Permissions.

