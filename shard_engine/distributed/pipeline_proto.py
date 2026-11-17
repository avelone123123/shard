import hmac
import json
import socket
import struct
try:
    import torch
except Exception:
    torch = None
MAGIC = b"SHPT"
PROTO_VER = 1
MAX_FRAME_BYTES = 128 * 1024 * 1024
MSG_AUTH = 1
MSG_AUTH_OK = 2
MSG_AUTH_ERR = 3
