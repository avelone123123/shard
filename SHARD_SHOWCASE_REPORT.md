# SHARD — GitHub Showcase & Promo Video Report

## 1. Summary of Deliverables

A complete open-source GitHub showcase overhaul and a vertical `1080x1920` `30fps` promotional MP4 video were implemented on dedicated branch `chore/github-showcase-and-promo-video` (without altering `website/`, PR `#3`, `master`, or user caches).

---

## 2. GitHub Showcase Improvements & Metadata

### 2.1 `README.md` Overhaul
- Rewrote [README.md](README.md) into a structured developer product page grounded directly in verified `shard_engine/` and `native/` modules (`v1.8.1`):
  - Modern Slate Hero showcase (`assets/showcase/hero.png`), branding logo, live Shields badges (Website, PyPI, GitHub Releases, CI workflow, MIT License).
  - Embedded real captured walkthrough GIF (`assets/showcase/demo.gif`) and 6 real sanitized live screenshots (`assets/showcase/real-dashboard-*.png` and `assets/showcase/real-cli-*.png`).
  - Honest architectural diagram (`assets/showcase/shard-architecture.svg`) and end-to-end workflow diagram (`assets/showcase/shard-workflow.svg`).
  - Verified sections covering Pre-Flight Sizing (`fit.py` / `shard fit`), Dual GGUF & PyTorch execution (`gguf_backend.py` / `engine.py`), OpenAI & Anthropic compatible APIs (`server.py`), C++20/MASM AVX2 kernels (`native/`), Hardware requirements, PyPI/Docker/source installation, Roadmap, Contributing, License, and documentation links.
  - Explicit **Experimental** boundaries for multi-host RPC over lossy/WAN links and `net_*` P2P NAT traversal / ChaCha20 Blind Relays / WireGuard plans (`docs/SHARD_NETWORK.md`), alongside the **single-node host** boundary and Python `>=3.11,<3.14` scope of SOUP fine-tuning (`docs/COLLABORATIVE_TRAINING.md`).

### 2.2 Visual Assets Created or Updated
- `assets/showcase/github-social-preview.png` (`1280x640` exact GitHub Social Preview banner in Modern Slate styling).
- `assets/showcase/hero.png` (`1600x900` dark developer workstation hero banner composited with real SHARD UI and `shard fit` CLI output).
- `assets/showcase/demo.gif` (`1000x625`, `154` frames cycling through real captured CLI and Modern Slate Web UI workflows).
- `assets/showcase/shard-architecture.svg` (`1400x840` color-coded module topology separating Core Runtime, Optional SOUP Training, and Experimental P2P/Blind Relay subsystems).
- `assets/showcase/shard-workflow.svg` (`1400x460` 5-stage runtime pipeline + 5-stage SOUP improvement loop).
- Real live captured screenshots under `assets/showcase/` (`real-dashboard-overview.png`, `real-dashboard-connect.png`, `real-dashboard-network.png`, `real-dashboard-lan.png`, `real-dashboard-training.png`, `real-dashboard-server.png`, `real-cli-fit.png`, `real-cli-doctor.png`, `real-cli-status.png`), with personal usernames, private subnet IPs, and Node IDs sanitized.
- Synced `assets/screenshots/dashboard-overview.png` and `assets/screenshots/cli-fit-advisor.png` with authentic live captures (replacing prior synthetic mockups).

### 2.3 GitHub Repository Metadata (`gh repo edit`)
- Added `openai-compatible` and `python` topics to reach GitHub's full 20-topic discovery matrix (`avx2`, `fastapi`, `gguf`, `huggingface`, `inference`, `llama-cpp`, `llm`, `local-llm`, `offline`, `self-hosted`, `vram`, `windows`, `distributed-inference`, `fine-tuning`, `local-ai`, `lora`, `p2p`, `pytorch`, `openai-compatible`, `python`).
- Verified repository homepage (`https://avelone123123.github.io/shard/`) and description accurately note experimental P2P/WAN relay status.
- Note on Repository Social Preview: GitHub does not expose an API endpoint to upload custom repository OpenGraph social preview images programmatically; `assets/showcase/github-social-preview.png` (`1280x640`) is ready for manual upload under `Settings -> General -> Social preview`.

---

## 3. Promo Video Production (`artifacts/SHARD_promo.mp4`)

### 3.1 Toolchain & Reproducible Source (`video/`)
- **Composition Engine**: Remotion `4.0.544` (`video/src/Root.tsx`, `video/src/Scenes.tsx`, `video/src/ShardPromo.tsx`, `video/src/styles.ts`, `video/src/timing.json`) running headless Chromium at `1080x1920`, `30 FPS`.
- **Neural Voiceover (`video/scripts/build_audio.py`)**: Open-weight local `kokoro-v1.0.fp16.onnx` (`am_michael`, American English, speed `1.12`) generating phrase-level clips synced frame-accurately via `video/src/timing.json`.
- **Original Music & Sound Design (`video/scripts/build_audio.py`)**: Synthesized `48 kHz` stereo dark electronic ambient soundtrack (`numpy` + `scipy`) featuring filtered sub-bass pulses, minor atmospheric chords, digital beat ticks, scene-transition transients, and automatic speech-envelope ducking, normalized and muxed via FFmpeg `v7.1` (`video/scripts/mux_audio.mjs`).
- **Re-Render Commands**:
  1. `python video/scripts/build_audio.py`
  2. `cd video && npm install && npm run render`

### 3.2 Scenario Structure (`59.27s` / `1778` frames)
1. **01 // Cloud Lock-In (`0.25s - 3.85s`)**: Dark GPU hardware backdrop, kinetic headline on cloud lock-in and API token fees.
2. **02 // Meet SHARD (`3.85s - 9.95s`)**: Animated hexagonal SHARD wireframe emblem, `v1.8.1` badge, and core capability grid.
3. **03 // Pre-Flight Fit & Dashboard (`9.95s - 17.92s`)**: Live `shard fit` terminal table transitioning to the real Modern Slate Workstation catalog.
4. **04 // Developer Infrastructure (`17.92s - 33.15s`)**: Animated CLI prompt, real IDE/agent connection generator, GGUF/PyTorch/`C++20 AVX2` specs, and live Python OpenAI/Anthropic snippet.
5. **05 // Distributed Compute (`33.15s - 44.35s`)**: Animated `UDP :8001 + ggml-rpc` LAN memory-pool topology, real LAN tab UI, and explicit amber **Experimental** callout for Ed25519 P2P / ChaCha20 Blind Relays.
6. **06 // SOUP Fine-Tuning (`44.35s - 53.18s`)**: Real SOUP training dashboard tab (`SINGLE-NODE HOST`) paired with the 5-step highlighted feedback-to-rollback pipeline.
7. **07 // Final Reveal (`53.18s - 59.27s`)**: Hexagonal emblem reveal, *"Your models. Your hardware. Your infrastructure."*, GitHub URL, website URL, and `pip install shard-engine`.

---

## 4. Verification Results

All checks below ran on the final committed deliverables (`artifacts/SHARD_promo.mp4` and repository files):
- **`ffprobe` Stream Audit**: Verified `artifacts/SHARD_promo.mp4` (`1080x1920`, `9:16`, `30/1 FPS`, `1778` frames, `59.266667s`, video codec `h264` High `yuv420p` `~1.30 Mbps`, audio codec `aac` LC `48000 Hz` stereo `~195 kbps`, `+faststart`).
- **`ffmpeg blackdetect` Check**: `0` unintended black segments (`blackdetect` output empty).
- **Visual & Audio Review**: Inspected all keyframes across `00:00 - 00:58` (ensuring zero caption overlap, clear safe zones, crisp rendering, zero secrets/tokens) and audited full English voiceover + ducked soundtrack mix.
- **`README.md` Audit**: Zero broken local relative links and `0` emojis.
- **Pull Request**: Branch `chore/github-showcase-and-promo-video` pushed cleanly with PR opened against `master`.
