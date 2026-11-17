import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from . import net_identity, paths, storage
_IS_WIN = sys.platform == "win32"
OFFICIAL_RADMIN_URL = "https://download.radmin-vpn.com/download/files/Radmin_VPN_2.1.4952.2.exe"
OFFICIAL_RADMIN_VERSION = "2.1.4952.2"
RADMIN_CONFIG_FILE = "radmin_state.json"
def _radmin_config_path() -> str:
    return os.path.join(paths.config_dir(), RADMIN_CONFIG_FILE)
def _load_radmin_pref() -> dict:
    return storage.read_json_safe(_radmin_config_path(), default={"consent": None, "declined": False, "installed_at": None, "last_error": None}, expected_type=dict)
