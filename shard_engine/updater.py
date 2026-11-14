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
