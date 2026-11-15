# SHARD Official Website Visual Redesign Report
## Modern Slate × Premium Developer Infrastructure × 3D Distributed Compute Core

### 1. Executive Summary
The official marketing and documentation website for the SHARD project (`https://avelone123123.github.io/shard/`) has been completely redesigned. The previous visual language (bright beige `#f5f2eb`, high-contrast borders, serif typography) was replaced with **Modern Slate × Premium Developer Infrastructure**, aligning the website with the SHARD application dashboard.

Additionally, a custom Three.js 3D experience—the **SHARD Distributed Compute Core**—has been integrated into the hero section, visualizing layer-sharded model weights hopping between the central cluster master and distributed worker nodes.

All interactive components, multilingual data (`en`, `ru`, `kz`), model calculators, architecture explorers, CLI tabs, and theme toggles were preserved and verified across all standard responsive breakpoints (375px, 768px, 1024px, 1440px).

---

### 2. Design System Architecture & Token Mapping

#### 2.1 Palette Specifications
- **Background Core (`--bg-primary`)**: `#16181D` (Restrained dark graphite slate)
- **Secondary Surfaces (`--bg-secondary`, `--bg-card`)**: `#1D2129` (Technical card background)
- **Elevated Surfaces (`--bg-surface-elevated`, `--bg-card-hover`)**: `#232730` (Interactive card hover & dropdowns)
- **Sunken / Terminal Surfaces (`--bg-surface-sunken`, `--bg-code`)**: `#101216` & `#121418` (Code snippets & terminal blocks)
- **Border Hierarchy**:
  - Subdued: `--border-light: #252A34`
  - Structural: `--border-primary: #2E3440`
  - Emphasized: `--border-strong: #3C4352`
- **Accent Palette**:
  - Primary Teal Accent: `--accent-primary: #0F766E`
  - Vibrant Edge / Indicator: `--accent-light: #14B8A6`
  - Text Highlights: `--accent-text: #39B8A8`
  - Subtle Tints & Glows: `--accent-tint: rgba(15, 118, 110, 0.14)`, `--accent-glow: rgba(20, 184, 166, 0.25)`
  - Soft Badges: `--accent-soft: #183233`, `--accent-soft-text: #5EEAD4`, `--accent-border: #265E5B`
- **System Telemetry Colors**:
  - Success / Online: `--success: #52D185`
  - Warning / Memory Pressure: `--warning: #F0B347`
  - Danger / Insufficient RAM: `--danger: #F27474`

#### 2.2 Alternate Terminal / High-Contrast Theme (`[data-theme="terminal"]`)
The theme switcher dynamically flips between **Modern Slate** (default) and **Terminal High-Contrast**:
- Background: `#0D0F12`
- Accent: `#00F5D4` (High-visibility neon cyan)
- Text Code: `#00F5D4`
- Borders: `#1F2937` with `#00F5D4` glowing focus rings

#### 2.3 Typography Architecture
- **Display & Headings**: `Space Grotesk` (geometric, technical, modern)
- **Body & Interface**: `Inter` (neutral, legible, high x-height)
- **Code, Telemetry & Math**: `JetBrains Mono` (tabular numbers, programming ligatures)

---

### 3. Subtle 3D Experience — SHARD Distributed Compute Core

The Hero visual section embeds a lightweight, interactive Three.js 3D visualization:

1. **Geometry & Structure**:
   - **Central Master Node**: Wireframe icosahedron (`THREE.IcosahedronGeometry(2.4, 1)`) with an internal rotating octahedron core (`THREE.OctahedronGeometry(1.4, 0)`), representing the llama-server coordinator and tensor splitter.
   - **Distributed Worker Satellites**: 4 outer octahedron nodes positioned in 3D coordinate space representing pooled LAN GPU VRAM / RAM contributors.
   - **Neural Interconnects**: Vector lines linking the master coordinator to each satellite node.
   - **Activation Data Pulses**: Orbiting glowing spheres (`THREE.SphereGeometry(0.16, 8, 8)`) moving along each line to visualize continuous token activation streaming (~8 KB hops).

2. **Interactivity & Parallax**:
   - Pointer / mousemove parallax with smooth damping (`rootGroup.rotation.y += (targetRotY - rootGroup.rotation.y) * 0.05`).
   - Constant gentle ambient rotation of the cluster.
   - Counter-rotation of the inner core mesh.

3. **Performance & Accessibility Optimization**:
   - `IntersectionObserver`: Pauses the animation loop when the hero section scrolls out of the viewport, reducing idle GPU usage to 0%.
   - `prefers-reduced-motion`: When detected via `window.matchMedia`, renders a clean, static 3D orientation without continuous frame requests.
   - WebGL Graceful Fallback: If WebGL is unavailable or script execution fails, the layout displays `hero-fallback-img` (`assets/animations/hero-network.gif`) without breaking layout or logging uncaught exceptions.

---

### 4. Interactive Components Verification

All functionality across the website was verified with automated Playwright browser tests:

| Component | Status | Details |
| :--- | :--- | :--- |
| **Multilingual Switcher** | Pass | Tested across `en`, `ru`, `kz`. Data keys and dynamic labels update reactively. |
| **Theme Toggle** | Pass | Toggles between Modern Slate (authoritative baseline) and Terminal High-Contrast. |
| **Hardware Fit Simulator** | Pass | Selects models (Qwen 1.5B - Llama 3.3 70B), simulates VRAM/RAM allocations, computes `-ts` split, updates status badge. |
| **Architecture Inspector** | Pass | Interactive node selection loads subsystem name, role, details, protocols, and file paths. |
| **CLI Reference Tabs** | Pass | 5 active tabs (`Server`, `Models`, `LAN`, `Network`, `Training`) switch panels cleanly. |
| **Install Command Tabs** | Pass | `Pip (Source)`, `Docker Compose`, `Native C++20` code panels switch and copy properly. |
| **Copy-to-Clipboard** | Pass | Clipboard API copies command text with visual checkmark SVG feedback. |
| **FAQ Accordion** | Pass | Accordion opens and closes smoothly with chevron rotation. |
| **Responsive Navigation** | Pass | Desktop nav displays all 9 sections; mobile displays compact drawer with hamburger toggle. |

---

### 5. Cross-Viewport Visual Audit

Automated Playwright captures verified layout consistency across viewports:
- **Mobile (375 × 667)**: Clean single-column layout, touch-friendly inputs, hamburger navigation drawer, stacked hero CTAs, zero horizontal scroll.
- **Tablet (768 × 1024)**: 2-column workflow and features grid, inline controls, hero 3D core rendered below title.
- **Small Desktop (1024 × 768)**: Unified header with full navigation bar, 1-column hero layout with expanded 3D frame, 2-column features grid.
- **Large Desktop (1440 × 900)**: Full 2-column hero grid (1.1fr / 0.9fr) with interactive 3D compute core on the right, 3-column bento grids, 4-column feature matrix, 2-column architecture explorer.

Console Error Count: **0 errors**.

---

### 6. Deliverables & Git Summary
- **Target Branch**: `website-modern-slate-redesign`
- **Core Files Updated**:
  - `website/index.html` (961 lines, 0 comments, 0 empty lines)
  - `website/styles.css` (1438 lines, 0 comments, 0 empty lines)
  - `website/app.js` (328 lines, 0 comments, 0 empty lines)
- **Commit**: `1945dac` (`feat(website): complete Modern Slate redesign with 3D Distributed Compute Core`)
- **PR Status**: Branch ready to be pushed to remote and opened via GitHub CLI.
