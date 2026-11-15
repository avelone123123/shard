# SHARD PyPI Release & Packaging Report
## Packaging Verification, Distribution Audit & OIDC Trusted Publishing Diagnostics

**Project**: SHARD (`shard-engine`)  
**Version**: `v1.8.1`  
**Distribution Files**:  
- `dist/shard_engine-1.8.1-py3-none-any.whl` (114,892 bytes)  
- `dist/shard_engine-1.8.1.tar.gz` (110,412 bytes)  
**Release Engineer**: Lead Software Architect & Release Engineer  
**Date**: November 2026  

---

## 1. Release Status & Truthful Assessment

- **GitHub Release**: Successfully created and published as `v1.8.1` with release assets (`shard_engine-1.8.1-py3-none-any.whl` and `shard_engine-1.8.1.tar.gz`) attached:  
  `https://github.com/avelone123123/shard/releases/tag/v1.8.1`
- **GitHub Actions Test Gate**: Pre-publish test job (`test-before-publish`) ran all unit tests on Windows and completed with 100% pass rate (`43748754090`).
- **Clean Environment Wheel Installation**: Tested in an isolated temporary venv outside the repository. CLI verified: `shard --version` reports `shard-engine 1.8.1` and `shard doctor` reports environment ready.
- **PyPI Publication State**: The GitHub Actions OIDC token exchange was executed by `pypa/gh-action-pypi-publish@release/v1`. PyPI responded with:
  ```
  Token request failed: the server refused the request for the following reasons:
  * invalid-publisher: valid token, but no corresponding publisher (Publisher with matching claims was not found)
  ```
  This proves the GitHub Actions workflow successfully generated and sent valid OIDC claims to PyPI. To complete the final handshake, PyPI requires registering the "Pending Publisher" once in the user's PyPI account.

---

## 2. One-Time PyPI Configuration Instructions (Pending Publisher)

To enable automatic publication to PyPI from GitHub Actions:

1. Open: [https://pypi.org/manage/account/publishing/](https://pypi.org/manage/account/publishing/)
2. Scroll to **"Add a publisher"** and enter:
   - **PyPI Project Name**: `shard-engine`
   - **Owner**: `avelone123123`
   - **Repository name**: `shard`
   - **Workflow name**: `publish.yml`
   - **Environment name**: `pypi`
3. Click **"Add publisher"**.
4. Re-run the publishing workflow using GitHub CLI:
   ```powershell
   gh workflow run publish.yml --ref master
   ```
   Or trigger a new release. The package will instantly publish to `https://pypi.org/project/shard-engine/`.

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
