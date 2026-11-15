# SHARD PyPI Release & Packaging Report
## Packaging Verification, Distribution Audit & OIDC Trusted Publishing Success

**Project**: SHARD (`shard-engine`)  
**Version**: `v1.8.1`  
**Distribution Files**:  
- `dist/shard_engine-1.8.1-py3-none-any.whl` (114,892 bytes)  
- `dist/shard_engine-1.8.1.tar.gz` (110,412 bytes)  
**PyPI URL**: [https://pypi.org/project/shard-engine/1.8.1/](https://pypi.org/project/shard-engine/1.8.1/)  
**Lead Software Architect & Release Engineer**: AI Engineering Team  
**Date**: November 2026  

---

## 1. Release Status: 100% Published & Verified

- **PyPI Official Release**: Package `shard-engine` is live on the public PyPI index.
- **PyPI JSON API Probe**: `GET https://pypi.org/pypi/shard-engine/json` returns `HTTP 200 OK` (`info.version == "1.8.1"`).
- **Public `pip` Installation Test**: Verified by creating an isolated virtual environment outside the repository and executing:
  ```powershell
  pip install --no-cache-dir shard-engine==1.8.1
  ```
  Installation completed successfully, and both `shard --version` and `shard doctor` executed cleanly.
- **GitHub Actions Test Gate**: Pre-publish test job (`test-before-publish`) ran all unit tests on Windows and completed with 100% pass rate (`43750133691`).
- **OIDC Trusted Publishing**: Successfully exchanged cryptographic GitHub Actions OIDC identity token with PyPI without long-lived API secrets.

---

## 2. Public Installation Reference

Any user can now install SHARD directly from PyPI:

```bash
# Core install: CLI, FastAPI server, web dashboard, GGUF (llama.cpp) + LAN clustering
pip install shard-engine

# Full install: adds PyTorch/Transformers safetensors & Hugging Face extras
pip install "shard-engine[full]"

# Verification
shard --version
shard doctor
```

---

## 3. Packaging Artifacts & Distribution Inspection

Both source distribution (`sdist`) and pure-Python wheel (`bdist_wheel`) were built using `python -m build` and validated using `twine check`.

### 3.1 Wheel Content Audit
Inspection of `dist/shard_engine-1.8.1-py3-none-any.whl` confirms the presence of all required runtime assets:

| Archive Path | Verification | Description |
| :--- | :--- | :--- |
| `shard_engine/__init__.py` | Declares `__version__ = "1.8.1"` | Single source of truth version identifier |
| `shard_engine/session_registry.py` | Added in v1.8.1 | Cryptographic session registry with token hashing and TTL |
| `shard_engine/server.py` | Updated in v1.8.1 | Hardened participant resolution, session endpoints, concurrency file locks |
| `shard_engine/lan.py` | Updated in v1.8.1 | `LanManager.is_open()` safe accessor and safe CP1252 output |
| `shard_engine/dashboard.html` | Updated in v1.8.1 | Auto-session issuance and pairing prompt handling (100% parity) |
| `shard_engine-1.8.1.dist-info/METADATA` | Package metadata | Dependencies, classifiers, and project descriptions |
| `shard_engine-1.8.1.dist-info/entry_points.txt` | Entry point mapping | Maps `shard = shard_engine.cli:main` |

### 3.2 Twine Validation
```
Checking dist\shard_engine-1.8.1-py3-none-any.whl: PASSED
Checking dist\shard_engine-1.8.1.tar.gz: PASSED
```

---

## 4. GitHub Actions Trusted Publishing Workflow

```yaml
name: Publish to PyPI
on:
  release:
    types: [published]
  workflow_dispatch:
jobs:
  test-before-publish:
    name: Run test suite before publish
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install dependencies
        run: pip install -e .
      - name: Run test discovery
        run: python -m unittest discover -s . -p "tests_*.py" -v
  build-and-publish:
    name: Build distributions and publish to PyPI
    needs: test-before-publish
    runs-on: ubuntu-latest
    environment:
      name: pypi
      url: https://pypi.org/p/shard-engine
    permissions:
      id-token: write
      contents: read
    steps:
    - uses: actions/checkout@v4
    - name: Set up Python
      uses: actions/setup-python@v5
      with:
        python-version: "3.11"
    - name: Install build tools
      run: python -m pip install --upgrade pip build twine
    - name: Build distributions
      run: python -m build
    - name: Validate distribution integrity
      run: python -m twine check dist/*
    - name: Publish to PyPI
      uses: pypa/gh-action-pypi-publish@release/v1
```
