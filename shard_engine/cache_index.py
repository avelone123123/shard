import os
from typing import Dict, List, Optional
class LocalCacheEntry:
    def __init__(self, model_id: str, local_path: str, file_size: int, mtime: float):
        self.model_id = model_id
        self.local_path = local_path
        self.file_size = file_size
        self.mtime = mtime
