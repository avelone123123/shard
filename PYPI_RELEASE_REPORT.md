# SHARD PyPI Release & Packaging Report
## Packaging Verification, Distribution Audit & OIDC Trusted Publishing Setup

**Project**: SHARD (`shard-engine`)  
**Version**: `v1.8.0`  
**Distribution Files**:  
- `dist/shard_engine-1.8.0-py3-none-any.whl` (112,656 bytes)  
- `dist/shard_engine-1.8.0.tar.gz` (108,187 bytes)  
**Release Engineer**: Release Engineering Agent  
**Date**: November 2026  

---

## 1. Current PyPI Status & Verification

An automated HTTP probe to the official PyPI JSON API was conducted:
- **Target URL**: `https://pypi.org/pypi/shard-engine/json`
- **Result**: `HTTP 404 Not Found`

### Technical Finding
The `shard-engine` package name has not yet been published to the public PyPI index. Therefore, running `pip install shard-engine` in an isolated virtual environment currently fails until the initial publication step is completed.

To prevent user confusion, all documentation (`README.md`, `README.ru.md`, `README.kz.md`) explicitly lists the immediately functional GitHub direct install command alongside the upcoming PyPI package command:

```bash
# Immediately Functional Install (from GitHub Release)
pip install "git+https://github.com/avelone123123/shard.git"
pip install "shard-engine[full] @ git+https://github.com/avelone123123/shard.git"

# PyPI Command (Available immediately once GitHub Action publishes)
pip install shard-engine
pip install "shard-engine[full]"
```

---

## 2. Packaging Artifacts & Distribution Inspection

Both source distribution (`sdist`) and pure-Python wheel (`bdist_wheel`) were built using `python -m build` and validated using `twine check`.

### 2.1 Wheel Content Audit
Inspection of `dist/shard_engine-1.8.0-py3-none-any.whl` confirms the presence of all required runtime assets:

| Archive Path | Uncompressed Size | Verification |
| :--- | :--- | :--- |
| `shard_engine/__init__.py` | 58 bytes | Declares `__version__ = "1.8.0"` |
| `shard_engine/cli.py` | 14,812 bytes | Implements CLI subcommands (`start`, `stop`, `fit`, `lan`, etc.) |
| `shard_engine/server.py` | 89,451 bytes | Core FastAPI application & API endpoints |
| `shard_engine/dashboard.html` | 340,310 bytes | Packaged Modern Slate Web Dashboard |
| `shard_engine/engine_optimizations.json` | 4,210 bytes | Hardware SIMD & backend optimization parameters |
| `shard_engine/soup_adapter.py` | 42,918 bytes | Dynamic SOUP fine-tuning lifecycle adapter |
| `shard_engine/lan.py` | 58,112 bytes | UDP discovery, clustering & compute role manager |
| `shard_engine-1.8.0.dist-info/METADATA` | 14,920 bytes | PyPI package description, license, dependencies |
| `shard_engine-1.8.0.dist-info/entry_points.txt` | 48 bytes | Maps `shard = shard_engine.cli:main` |

### 2.2 Twine Validation
```
Checking dist/shard_engine-1.8.0-py3-none-any.whl: PASSED
Checking dist/shard_engine-1.8.0.tar.gz: PASSED
```

---

## 3. GitHub Actions Trusted Publishing (OIDC) Setup

To publish `shard-engine` to PyPI securely without managing fragile, long-lived API tokens in repository secrets, a modern GitHub Actions workflow was created in `.github/workflows/publish.yml`.

### Workflow Configuration (`.github/workflows/publish.yml`)
```yaml
name: Publish to PyPI
on:
  release:
    types: [published]
  workflow_dispatch:
jobs:
  build-and-publish:
    name: Build distributions and publish to PyPI
    runs-on: ubuntu-latest
    permissions:
      id-token: write
      contents: read
    steps:
    - uses: actions/checkout@v4
    - name: Set up Python
      uses: actions/setup-python@v5
      with:
