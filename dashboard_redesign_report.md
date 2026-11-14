# SHARD Dashboard Visual Redesign Report

**Target**: `shard_engine/dashboard.html` and synchronized root `dashboard.html` (public landing site `website/index.html` and backend APIs untouched)  
**Design Direction**: Vintage Computing × Modern Developer Workstation (grounded in `bergside/awesome-design-skills` — `skills/vintage/SKILL.md` and `DESIGN.md`)  
**Date**: 2026-11-14

---

## 1. Initial Visual & CSS Audit (BEFORE Baseline)

Isolated server `uvicorn shard_engine.server:app --host 127.0.0.1 --port 18090` (`SHARD_DATA_DIR=%TEMP%\shard_accept_env\data`) audited via Playwright headless Chromium at `1440x900` (desktop) and `1024x768` (tablet) prior to edits:

1. **Aesthetic & Hierarchy Problems**:
   - Generic dark-neon theme (`rgb(12, 14, 20)` / `#0c0e14`, `#131620`, neon cyan `#38bdf8`, blurred modal backdrops `blur(6px)`), relying on system UI font stack across headings and telemetry.
   - `before_report.json` console error on load: `404 (Not Found)` due to missing `/favicon.ico`.
2. **Broken / Missing CSS Definitions**:
   - `.info-card` (used on `API Ключи`, `Сеть`, `Сервер SHARD`, `Обучение (SOUP)`) had zero CSS rules defined, leaving sections unframed on dark background.
   - `.btn-review` (used in `renderDownloadedModelsGrid`) had zero CSS rules defined, falling back to browser default button styling.
   - `var(--purple)` / `var(--purple-bg)` used by the `"SSD СТРИМИНГ (NVMe)"` badge in `renderCatalogGrid` were undefined CSS variables.
   - `.ep-val` wrapped endpoint URLs mid-word (`.../v1/chat/comp` + `letions`).
   - Hardcoded inline dark hex colors and hex-alpha concatenation (`modeColor + '44'` in `lanRefresh()`) embedded directly in JS DOM builders.
3. **Layout & Responsive Defects**:
   - `1024x768` tablet Market view had horizontal overflow: `scrollWidth: 1034 > innerWidth: 1024` (`overflowX: true`), and squeezed the search bar next to category buttons.
   - `#srvCreateOwnerBox` on the `Сервер SHARD` tab used an unlabeled 4-column input row without explicit field labels.
   - Root `dashboard.html` and `shard_engine/dashboard.html` had diverged earlier in whitespace/CRLF formatting, causing `tests_phase_i_product_hardening.py` `test_09_dashboard_regression_bugs_fixed` to fail before edits.

---

## 2. Design Decisions & Final Design Tokens

To replace the dark-neon aesthetic without harming developer readability during long sessions, classic 1980s–1990s workstation chrome (`#D4D0C8`, `#008080` Teal) was paired with warm paper-cream work surfaces (`#ECEAE3`, `#F5F3EC`) and subtle dot-matrix canvas texture, while preserving dark phosphor terminal wells (`#18201F`) for code snippets, audit logs, and installation scripts.

### Final `:root` Token System (`shard_engine/dashboard.html` lines 12–63)

| Token | Value | Role |
|---|---|---|
| `--bg` | `#ECEAE3` | Warm workstation backdrop with subtle `1px` dot grid |
| `--surface` | `#DCD9CF` | Recessed chrome buttons & tab rails |
| `--surface-chrome` | `#D4D0C8` | Sticky header, hardware strip, modal & table headers |
| `--surface-elevated` | `#F5F3EC` | Cards, panels, modals, text inputs |
| `--surface-sunken` | `#E3E0D6` | Inset subcards, wells, metadata strips |
| `--surface-terminal` | `#18201F` | Dark phosphor terminal/log/code container |
| `--text` / `--text-secondary` / `--text-muted` | `#111315` / `#373D40` / `#52585C` | Ink hierarchy on light surfaces |
| `--text-terminal` | `#E2ECE9` | Crisp text inside `#18201F` terminal blocks |
| `--accent` / `--accent-hover` / `--accent-active` | `#008080` / `#006666` / `#004D4D` | Classic Teal primary actions & active state |
| `--accent-text` / `--accent-soft` / `--accent-border` | `#005C5C` / `#D6ECEB` / `#2B8A8A` | Accessible teal text & tinted focus/selection surfaces |
| `--border` / `--border-strong` | `#A8A49A` / `#6E6B64` | 1px–2px structural framing with crisp inset bevels |
| `--success` / `--success-bg` / `--success-border` | `#146C35` / `#DCFCE7` / `#74C68E` | Verified status & passing preflight |
| `--warning` / `--warning-bg` / `--warning-border` | `#9A4707` / `#FEF3C7` / `#E5A93B` | Notice / Hybrid RAM+VRAM / update warning |
| `--danger` / `--danger-bg` / `--danger-border` | `#B91C1C` / `#FEE2E2` / `#E88B8B` | Error / destructive / rollback actions |
| `--info` / `--info-bg` / `--info-border` | `#0F5E6E` / `#E0F2F4` / `#7AB8C2` | SSD Cache / NVMe Streaming / LAN info pills |
