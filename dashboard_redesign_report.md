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
| `--font-display` | `'Silkscreen', 'JetBrains Mono', monospace` | Brand wordmark, badges, micro uppercase labels |
| `--font-mono` | `'JetBrains Mono', 'Cascadia Mono', Consolas, monospace` | Telemetry metrics, model metadata, logs, code |
| `--font-sans` | `'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, Helvetica, Arial, sans-serif` | Primary body copy & UI labels |
| Type scale | `12px / 14px / 16px / 20px / 24px / 32px` | Strict `--fs-xs` .. `--fs-2xl` tokens |
| Spacing & radius | `4 / 8 / 12 / 16 / 24 / 32px`, `2px / 4px` | Crisp `4px` spacing grid & tactile workstation corners |

### Programmatic WCAG 2.2 Contrast Ratios (21/21 pairs `>= 4.5:1`)
- `--text` on `--bg` (`15.47:1`), on `--surface-elevated` (`16.77:1`), on `--surface` (`13.18:1`), on `--surface-chrome` (`12.11:1`).
- `--text-secondary` on `--surface-elevated` (`9.93:1`), on `--surface` (`7.81:1`).
- `--text-muted` on `--surface-elevated` (`6.50:1`), on `--bg` (`6.00:1`), on `--surface` (`5.11:1`).
- `--accent-text` on `--surface-elevated` (`7.04:1`), on `--bg` (`6.50:1`); `#FFFFFF` on `--accent` (`4.77:1`); `#FFFFFF` on `--accent-hover` (`6.79:1`); `--accent-active` on `--accent-soft` (`7.87:1`).
- Semantic badges: `--success` on `--success-bg` (`5.93:1`), `--warning` on `--warning-bg` (`5.75:1`), `--danger` on `--danger-bg` (`5.30:1`), `--info` on `--info-bg` (`6.39:1`), `#FFFFFF` on `--danger` (`6.47:1`).
- Terminal: `--accent-soft` on `#18201F` (`13.48:1`), `#FAF9F5` on `#18201F` (`15.75:1`).

---

## 3. Files Modified & Parity Check

- `shard_engine/dashboard.html` — redesigned CSS design system, semantic helper classes, inline SVG favicon, accessible modal `role="dialog"` + `aria-modal="true"` + `Escape` close handler, labeled Server Hub grid, responsive tablet breakpoints, and unified theme tokens across JS templates.
- `dashboard.html` — synchronized 100% byte-for-byte with `shard_engine/dashboard.html` (`315,175` bytes, `4,597` lines, `0` blank lines, `0` HTML/CSS comments, `0` emojis, `node --check` exit code `0`).

Note: Uncommitted pre-existing Phase I.1 edits (`pyproject.toml`, `shard_engine/soup_adapter.py`, `phase_i_1_acceptance_report.md`) were left intact and nothing was committed or pushed.

---

## 4. Components Redesigned & Visual Fixes

1. **Global Chrome & Navigation**: Sticky `#D4D0C8` workstation top bar, beveled SVG hex shard logo, `Silkscreen` pixel-display wordmark (`SHARD`), `#shardVersionPill` update badge, top-right hardware telemetry panel (`.hw-bar`), folder-tab style `.nav-tabs` merging into `#ECEAE3` canvas on active tab.
2. **Favicon & Keyboard Accessibility**: Inline data-URI SVG favicon eliminated the `404 /favicon.ico` error; `:focus-visible` outlines (`2px solid var(--accent)`, observed in Chromium as `rgb(0, 128, 128) solid 2px`), modal `role="dialog"` / `aria-modal="true"`, and `Escape` handler closing `#modelModalBackdrop`, `#dirModalBackdrop`, `#updateModalBackdrop`, `#browserModalBackdrop`.
3. **Model Market & Downloaded View**: Search wrapper spans full width on row 1 alongside storage directory & PC browser buttons, `.filter-tabs` (`flex: 1 1 100%`) sit cleanly on row 2; fixed missing `.btn-review` rule; replaced undefined `--purple` / `--purple-bg` with `--info` / `--info-bg` on `"SSD СТРИМИНГ (NVMe)"`; placed `SSD КЭШ` pill cleanly on `.model-author` metadata row.
4. **Connect & API**: Widened sidebar column (`375px`) with `11.5px` `.ep-val` so `http://localhost:8000/v1/chat/completions` renders cleanly on one line; phosphor `#18201F` script/config terminal blocks with high-contrast copy controls and ping card.
5. **Chat, Keys, Network/LAN, Server Hub**: Framed missing `.info-card` panels with `#6E6B64` borders and inset bevels; styled structured chat bubbles, collapsible reasoning panel, bottom-right `.toast` placement so `.hw-bar` telemetry is never covered; converted `lanRefresh()` hex-alpha concatenation (`+ '44'`) to semantic `.compat-pill` badges; added explicit `<label class="v-field-label">` tags above `#srvCreateOwnerBox` fields with responsive `.v-grid-4` -> 2-column -> 1-column breakpoints.
6. **Training (SOUP), Dataset Preview, Update & Model Browser Modals**: High-density `.trn-stat` telemetry cards, zebra-striped `.ds-table` + pagination strip (`.ds-pager`), crisp `.pf-item` preflight pass/warn indicators, drive & shortcut `.mb-root` pills, file inspector split pane (`.v-browser-split`), and light paper modals with subtle backdrop dimming instead of blurred dark glass.

---

## 5. Browser & Automated Test Verification

### Playwright Headless Chromium Verification (`task-5589`, after final edits)
- **Desktop (`1440x900`) & Tablet (`1024x768`)**:
  - `console_errors`: `[]` (`0` errors, down from `1` 404 before)
  - `page_errors`: `[]`
  - `http_errors`: `[]`
  - `scrollWidth` desktop `1440x900`: `1440` (`overflow1440: false`)
  - `scrollWidth` tablet `1024x768`: `1024` on Market (`after_11_tablet_market.png`) and `1024` on Training (`after_12_tablet_training.png`) (`overflowX: false`, fixing the BEFORE `1034 > 1024` overflow)
- **Interactive Triggers Exercised Live**:
  - Market search (`Qwen` / `Кодинг` filter count updated `107 -> 80`, `Скачанные на SSD` tab opened).
  - Model Card modal (`Обзор` on `Qwen2.5-Coder-1.5B-Instruct`): fetched 4,573-char spec & README (`after_13_model_card.png`), closed cleanly with `Escape`.
  - Connect & API: switched tool (`Cursor IDE`), toggled manual/script mode (`guideManualSection` visible), ran Ping test -> `"200 OK — Эндпоинт активен! Задержка: 15.0 мс | Ядро: shard-local-engine"`.
  - Network & LAN: switched `#netSubBtn-lan` (`#net-panel-lan` visible), rendered interface badges `[lan]`, `[radmin]`, UDP beacon (`after_15_network_lan.png`).
  - Server Hub: verified 4 labeled inputs (`['Имя сервера', 'Режим сети', 'Видимость', 'Регион']`) and 4 overview cards (`after_06_server.png`).
  - Training / SOUP: rendered live SOUP `v0.77.2`, `Python 3.12.16`, `23 одобр. / 0 ожид.` (`after_07_training_overview.png`), opened Dataset Preview (`ds-accept-e2e-ui-2-f72ab5`, `after_14_dataset_preview.png`), paginated page 1 (`показано 20 из 23`) -> page 2 (`показано 3 из 23`), ran Training Preflight -> `"Все проверки пройдены — готово к запуску обучения ... PASS"`, inspected job history (`after_08_training_jobs.png`).
  - Update Modal (`#shardVersionPill`): displayed `"Установленная версия: v1.8.0 | Новая на GitHub: v1.8.0"`, closed cleanly with `Escape` (`after_09_update_modal.png`).
  - Model Browser (`openModelBrowser('training')`): enumerated 7 roots (`C:\`, `D:\ (Ventoy)`, `Модели SHARD`, `Кэш Hugging Face`, `Обучение и чекпоинты SOUP`, `Загрузки`, `Домашняя папка`), closed cleanly with `Escape` (`after_10_model_browser.png`).

### Full Python Unit Test Suite (`task-5618`, after final edits)
- Command: `python -m unittest discover -s . -p "tests_*.py" -v`
- Result: **`Ran 171 tests in 38.609s — OK (skipped=1)`**
  - `170` passed, `0` failed (`test_09_dashboard_regression_bugs_fixed` verified byte-equality and required security/functional markers), `1` skipped (`test_wheel_contents_when_built` skipped because wheel not pre-built in `dist/`).

### Captured Visual Evidence (`scratch/redesign_shots/`)
- **BEFORE**: `before_01_market.png` .. `before_12_tablet_training.png`, metrics in `before_report.json`
- **AFTER**: `after_01_market.png` .. `after_17_downloaded_tab.png`, metrics in `after_report.json`

---

## 6. Classification & Remaining Notes

- **IMPLEMENTED & BROWSER VERIFIED**:
  - Complete Vintage Computing × Modern Developer Workstation palette, typography, bevels, dark phosphor terminal blocks, focus states, SVG favicon, modal dialogs & `Escape` closing across all 7 main tabs, subtabs, update modal, dataset preview/pagination, training preflight, model card modal, model browser, toast repositioning, and responsive desktop (`1440x900`) + tablet (`1024x768`) layouts.
  - All 170 active unit tests passing after final edits.
- **NOT VERIFIED**:
  - Narrow phone viewport (`375px` width) and live streaming chat message bubble + SOUP live installer progress bar animations were redesigned in CSS/HTML tokens (`chat-loading-card`, `glb-*`, `@media (max-width:760px)`), but not triggered end-to-end against a loaded GGUF model or active installer run during this visual pass.
  - `Silkscreen` font uses Latin bitmap glyphs cleanly (`SHARD`, `RUNTIME`, `BASE ENDPOINT`), falling back automatically to uppercase `JetBrains Mono` for Cyrillic labels — expected font metric behavior, no clipping observed.
  - If editing `dashboard.html` through an IDE with Format-on-Save active, note the IDE's HTML formatter can re-inject CRLF and blank lines into open buffers; direct sync kept both copies cleanly at `0` blank lines.
- **BLOCKED**:
  - None (`website/index.html` landing page intentionally untouched per task scope).
