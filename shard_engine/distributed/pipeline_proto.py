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
MSG_PING = 4
MSG_PONG = 5
MSG_LOAD_STAGE = 6
MSG_STAGE_READY = 7
MSG_FORWARD = 8
MSG_FORWARD_RES = 9
MSG_RELEASE_KV = 10
MSG_UNLOAD = 11
MSG_ERROR = 12
MSG_BW_PROBE = 13
MSG_BW_ACK = 14
_HDR_STRUCT = struct.Struct(">4sBBHI")
_TENSOR_META_STRUCT = struct.Struct(">BBHI")
def _dtype_maps():
