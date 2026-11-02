import ctypes
import os
import sys
_base = os.path.dirname(os.path.abspath(__file__))
_lib_name = "shard_core.dll" if sys.platform == "win32" else "shard_core.so"
DLL_PATH = os.path.join(_base, _lib_name)
class ShardNativeBridge:
    def __init__(self):
        self.dll = None
        self.version = "Native Core Not Loaded"
        self.is_native_active = False
        if os.path.exists(DLL_PATH):
            try:
                self.dll = ctypes.CDLL(DLL_PATH)
                self.dll.shard_get_version.restype = ctypes.c_char_p
                self.version = self.dll.shard_get_version().decode("utf-8")
                self.dll.shard_benchmark_avx2.argtypes = [ctypes.c_int]
                self.dll.shard_benchmark_avx2.restype = ctypes.c_int
                self.dll.shard_dot_product.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), ctypes.c_int]
                self.dll.shard_dot_product.restype = ctypes.c_float
                self.dll.shard_dot_product_fast.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), ctypes.c_int]
                self.dll.shard_dot_product_fast.restype = ctypes.c_float
                self.dll.shard_scale_vector.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), ctypes.c_float, ctypes.c_int]
                self.dll.shard_rmsnorm_f32.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.c_float]
                self.dll.shard_swiglu_f32.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), ctypes.c_int]
                self.dll.shard_vec_add_f32.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), ctypes.c_int]
                self.dll.shard_silu_f32.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), ctypes.c_int]
                self.dll.shard_gelu_f32.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), ctypes.c_int]
                self.dll.shard_dequant_q4.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(ctypes.c_float), ctypes.c_float, ctypes.c_int]
                self.dll.shard_dequant_q8_0.argtypes = [ctypes.POINTER(ctypes.c_byte), ctypes.POINTER(ctypes.c_float), ctypes.c_float, ctypes.c_int]
                self.dll.shard_mmap_open.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint64)]
                self.dll.shard_mmap_open.restype = ctypes.c_void_p
                self.dll.shard_mmap_close.argtypes = [ctypes.c_void_p]
                self.dll.shard_mmap_close.restype = ctypes.c_int
                self.is_native_active = True
            except Exception as e:
                self.version = f"Load Error: {e}"
    def benchmark_avx2(self, iterations=5000):
        if not self.is_native_active:
            return -1
        return self.dll.shard_benchmark_avx2(iterations)
    def dot_product(self, list_a, list_b, fast=True):
        count = min(len(list_a), len(list_b))
        if count == 0:
            return 0.0
        if not self.is_native_active:
            return float(sum(x * y for x, y in zip(list_a[:count], list_b[:count])))
        arr_t = ctypes.c_float * count
        ca = arr_t(*list_a[:count])
        cb = arr_t(*list_b[:count])
        if fast and hasattr(self.dll, "shard_dot_product_fast"):
            return float(self.dll.shard_dot_product_fast(ca, cb, count))
        return float(self.dll.shard_dot_product(ca, cb, count))
    def rmsnorm(self, x, weights=None, eps=1e-5):
        count = len(x)
        if count == 0:
            return []
        arr_t = ctypes.c_float * count
        cx = arr_t(*x)
        cout = arr_t()
        cw = arr_t(*weights) if weights and len(weights) == count else None
        if self.is_native_active:
            self.dll.shard_rmsnorm_f32(cx, cw, cout, count, ctypes.c_float(eps))
            return list(cout)
        ss = sum(v * v for v in x) / count
        rms = 1.0 / ((ss + eps) ** 0.5)
        if weights:
            return [v * rms * w for v, w in zip(x, weights)]
        return [v * rms for v in x]
    def swiglu(self, gate, up):
        count = min(len(gate), len(up))
        if count == 0:
            return []
        arr_t = ctypes.c_float * count
        cg = arr_t(*gate[:count])
        cu = arr_t(*up[:count])
        cout = arr_t()
        if self.is_native_active:
            self.dll.shard_swiglu_f32(cg, cu, cout, count)
            return list(cout)
        import math
        return [(g / (1.0 + math.exp(-g))) * u for g, u in zip(gate[:count], up[:count])]
    def vec_add(self, list_a, list_b):
        count = min(len(list_a), len(list_b))
        if count == 0:
            return []
        arr_t = ctypes.c_float * count
        ca = arr_t(*list_a[:count])
        cb = arr_t(*list_b[:count])
        cout = arr_t()
        if self.is_native_active:
            self.dll.shard_vec_add_f32(ca, cb, cout, count)
            return list(cout)
        return [a + b for a, b in zip(list_a[:count], list_b[:count])]
    def silu(self, x):
        count = len(x)
        if count == 0:
            return []
        arr_t = ctypes.c_float * count
        cx = arr_t(*x)
        cout = arr_t()
        if self.is_native_active:
            self.dll.shard_silu_f32(cx, cout, count)
            return list(cout)
        import math
        return [v / (1.0 + math.exp(-v)) for v in x]
    def gelu(self, x):
        count = len(x)
        if count == 0:
            return []
        arr_t = ctypes.c_float * count
        cx = arr_t(*x)
        cout = arr_t()
        if self.is_native_active:
            self.dll.shard_gelu_f32(cx, cout, count)
            return list(cout)
        import math
        return [0.5 * v * (1.0 + math.tanh(0.7978845608 * (v + 0.044715 * v * v * v))) for v in x]
    def dequant_q4(self, raw_bytes, scale=1.0):
        count = len(raw_bytes) * 2
        out = (ctypes.c_float * count)()
        if not self.is_native_active:
            res = []
            for b in raw_bytes:
                res.append(((b & 0x0F) - 8) * scale)
                res.append((((b >> 4) & 0x0F) - 8) * scale)
            return res
        in_arr = (ctypes.c_ubyte * len(raw_bytes)).from_buffer_copy(raw_bytes)
        self.dll.shard_dequant_q4(in_arr, out, float(scale), count)
        return list(out)
    def dequant_q8(self, raw_bytes, scale=1.0):
        count = len(raw_bytes)
        out = (ctypes.c_float * count)()
        if not self.is_native_active:
            return [float(b) * scale for b in raw_bytes]
        in_arr = (ctypes.c_byte * count).from_buffer_copy(raw_bytes)
        self.dll.shard_dequant_q8_0(in_arr, out, float(scale), count)
        return list(out)
shard_native = ShardNativeBridge()