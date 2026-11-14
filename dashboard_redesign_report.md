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
