import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse
from . import __app_name__, __version__, paths, process, storage
GITHUB_REPO = os.environ.get("SHARD_GITHUB_REPO", "avelone123123/shard")
GITHUB_API_RELEASES_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_API_TAGS = f"https://api.github.com/repos/{GITHUB_REPO}/tags?per_page=15"
GITHUB_RAW_INIT = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/shard_engine/__init__.py"
PYPI_JSON_URL = f"https://pypi.org/pypi/{__app_name__}/json"
CACHE_TTL_SECONDS = 4 * 3600
MIN_UPDATE_FREE_BYTES = 150 * 1024 * 1024
_WIN_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_ALLOWED_HTTPS_HOSTS = frozenset({
    "api.github.com",
    "github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "codeload.github.com",
    "release-assets.githubusercontent.com",
    "pypi.org",
    "files.pythonhosted.org",
})
_SAFE_REF_RE = re.compile(r"^v?[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9._+-]{0,24})?$|^[0-9a-fA-F]{40}$")
_UPDATE_JOB_LOCK = threading.RLock()
_UPDATE_JOB_STATE = {
    "status": "idle",
    "phase": "idle",
    "progress_pct": 0,
    "message": "",
    "logs": [],
    "old_version": __version__,
    "new_version": None,
    "rolled_back": False,
    "restart_required": False,
    "backup_path": None,
    "error": None,
    "started_at": None,
