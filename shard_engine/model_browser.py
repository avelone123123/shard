import ctypes
import json
import os
import re
from pathlib import Path
from . import gguf_backend, hf_hub, paths, settings
_SENSITIVE_EXACT_NAMES = frozenset({
    ".ssh",
    ".gnupg",
    ".aws",
    ".azure",
    ".kube",
    ".docker",
    ".netrc",
    ".git-credentials",
    ".npmrc",
    ".pypirc",
    "system volume information",
    "$recycle.bin",
    "recovery",
    "owner_token.txt",
    "keys_db.json",
    "node_identity.json",
    "network_invites.json",
