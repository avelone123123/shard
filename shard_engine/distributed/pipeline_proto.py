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
    if torch is None:
        raise RuntimeError("PyTorch is required for tensor serialization")
    d2c = {
        torch.float32: 1,
        torch.float16: 2,
        torch.bfloat16: 3,
        torch.int64: 4,
    }
    c2d = {v: k for k, v in d2c.items()}
    return d2c, c2d
class ProtocolError(RuntimeError):
    pass
def recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Socket closed while reading frame")
        buf.extend(chunk)
    return bytes(buf)
def recv_exact_poll(sock, n, poll):
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except socket.timeout:
            poll()
            continue
        if not chunk:
            raise ConnectionError("Socket closed while reading frame")
        buf.extend(chunk)
    return bytes(buf)
def recv_frame_poll(sock, poll):
    hdr = recv_exact_poll(sock, _HDR_STRUCT.size, poll)
    magic, ver, msg_type, flags, length = _HDR_STRUCT.unpack(hdr)
    if magic != MAGIC or ver != PROTO_VER:
        raise ProtocolError("Invalid SHPT frame header")
    if length > MAX_FRAME_BYTES:
        raise ProtocolError(f"Frame length {length} exceeds limit")
    payload = recv_exact_poll(sock, length, poll) if length > 0 else b""
    return msg_type, flags, payload
def send_frame(sock, msg_type, payload=b"", flags=0):
    data = bytes(payload)
    if len(data) > MAX_FRAME_BYTES:
        raise ProtocolError(f"Frame exceeds max size ({len(data)})")
    hdr = _HDR_STRUCT.pack(MAGIC, PROTO_VER, int(msg_type), int(flags), len(data))
    sock.sendall(hdr + data)
def recv_frame(sock):
    hdr = recv_exact(sock, _HDR_STRUCT.size)
    magic, ver, msg_type, flags, length = _HDR_STRUCT.unpack(hdr)
    if magic != MAGIC or ver != PROTO_VER:
        raise ProtocolError("Invalid SHPT frame header")
    if length > MAX_FRAME_BYTES:
        raise ProtocolError(f"Frame length {length} exceeds limit")
    payload = recv_exact(sock, length) if length > 0 else b""
    return msg_type, flags, payload
def send_json_frame(sock, msg_type, obj, flags=0):
    raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    send_frame(sock, msg_type, raw, flags=flags)
def recv_json_frame(sock):
    msg_type, flags, payload = recv_frame(sock)
    obj = json.loads(payload.decode("utf-8")) if payload else {}
    return msg_type, flags, obj
def encode_tensor_payload(meta_dict, tensor):
    d2c, _ = _dtype_maps()
    meta_bytes = json.dumps(meta_dict, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    t = tensor.detach().contiguous().cpu()
    if t.dtype not in d2c:
        t = t.to(torch.float32)
    dtype_code = d2c[t.dtype]
    shape = list(t.shape)
    ndim = len(shape)
    if t.dtype == torch.bfloat16:
        raw_tensor = t.view(torch.uint16).numpy().tobytes()
    else:
        raw_tensor = t.numpy().tobytes()
    t_hdr = _TENSOR_META_STRUCT.pack(dtype_code, ndim, 0, len(meta_bytes))
    shape_bytes = struct.pack(f">{ndim}I", *shape) if ndim > 0 else b""
    return t_hdr + shape_bytes + meta_bytes + raw_tensor
def decode_tensor_payload(payload):
    _, c2d = _dtype_maps()
    if len(payload) < _TENSOR_META_STRUCT.size:
        raise ProtocolError("Truncated tensor payload")
    dtype_code, ndim, _, meta_len = _TENSOR_META_STRUCT.unpack(payload[:_TENSOR_META_STRUCT.size])
    pos = _TENSOR_META_STRUCT.size
    shape_len = ndim * 4
    if len(payload) < pos + shape_len + meta_len:
        raise ProtocolError("Truncated tensor shape/meta")
    shape = tuple(struct.unpack(f">{ndim}I", payload[pos:pos + shape_len])) if ndim > 0 else ()
    pos += shape_len
    meta = json.loads(payload[pos:pos + meta_len].decode("utf-8")) if meta_len > 0 else {}
    pos += meta_len
    raw = payload[pos:]
    dtype = c2d.get(dtype_code)
    if dtype is None:
        raise ProtocolError(f"Unsupported dtype code {dtype_code}")
    if len(raw) == 0:
        t = torch.empty(shape, dtype=dtype)
    elif dtype == torch.bfloat16:
        t = torch.frombuffer(bytearray(raw), dtype=torch.uint16).view(torch.bfloat16).reshape(shape).clone()
    else:
        t = torch.frombuffer(bytearray(raw), dtype=dtype).reshape(shape).clone()
    return meta, t
def client_auth_handshake(sock, token, cluster_id=""):
    send_json_frame(sock, MSG_AUTH, {"token": str(token or ""), "cluster_id": str(cluster_id or "")})
    mtype, _, obj = recv_json_frame(sock)
    if mtype != MSG_AUTH_OK:
        raise PermissionError(obj.get("error") or "Worker authentication rejected")
    return obj
def verify_token_constant_time(presented, expected):
    if not expected:
        return True
    p = str(presented or "")
    e = str(expected or "")
    if not p:
        return False
    return hmac.compare_digest(p, e)
