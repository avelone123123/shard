import os
from typing import Dict, List, Optional
class LocalCacheEntry:
    def __init__(self, model_id: str, local_path: str, file_size: int, mtime: float):
        self.model_id = model_id
        self.local_path = local_path
        self.file_size = file_size
        self.mtime = mtime
class LocalCacheIndex:
    def __init__(self, root_dirs: Optional[List[str]] = None):
        self.root_dirs = root_dirs or []
        self._entries: Dict[str, LocalCacheEntry] = {}
    def scan(self) -> Dict[str, LocalCacheEntry]:
        self._entries.clear()
        for root in self.root_dirs:
            if not os.path.isdir(root):
                continue
            for r, _, files in os.walk(root):
                for f in files:
                    if f.lower().endswith((".gguf", ".safetensors", ".bin")):
                        p = os.path.join(r, f)
                        try:
                            st = os.stat(p)
                            if st.st_size > 0:
                                entry = LocalCacheEntry(model_id=f, local_path=p, file_size=st.st_size, mtime=st.st_mtime)
                                self._entries[f] = entry
                        except OSError:
                            continue
        return self._entries
