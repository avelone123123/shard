import ctypes
import os
DLL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shard_core.dll")
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
                self.dll.shard_scale_vector.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), ctypes.c_float, ctypes.c_int]
                self.dll.shard_dequant_q4.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(ctypes.c_float), ctypes.c_float, ctypes.c_int]
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
    def dot_product(self, list_a, list_b):
        count = min(len(list_a), len(list_b))
        if count == 0:
            return 0.0
        if not self.is_native_active:
            return float(sum(x * y for x, y in zip(list_a[:count], list_b[:count])))
        arr_t = ctypes.c_float * count
        ca = arr_t(*list_a[:count])
        cb = arr_t(*list_b[:count])
        return float(self.dll.shard_dot_product(ca, cb, count))
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
shard_native = ShardNativeBridge()