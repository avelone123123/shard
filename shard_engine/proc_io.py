import ctypes
import locale
import os
import re
import subprocess
import threading
import time
_WIN_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_UTF8_MULTIBYTE_HINT_RE = re.compile(rb"(?:[\xd0-\xd1][\x80-\xbf]|\xe2(?:\x80|[\x94-\x97])[\x80-\xbf]|\xf0\x9f[\x80-\xbf]{2})")
def _win_ansi_encoding():
    try:
        enc = locale.getencoding() if hasattr(locale, "getencoding") else locale.getpreferredencoding(False)
    except Exception:
        enc = ""
    low = str(enc or "").strip().lower()
    if low and low not in ("utf-8", "utf8", "utf_8", "ascii"):
        return enc
    if os.name == "nt":
        try:
            acp = ctypes.windll.kernel32.GetACP()
            if acp and int(acp) != 65001:
                return f"cp{int(acp)}"
        except Exception:
            pass
