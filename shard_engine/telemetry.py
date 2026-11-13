import time
from typing import Dict, Any, List, Tuple
class DownloadTelemetrySnapshot:
    def __init__(self, downloaded_bytes: int, total_bytes: int, speed_bps: float, eta_sec: float, stage: str):
        self.downloaded_bytes = downloaded_bytes
        self.total_bytes = total_bytes
        self.speed_bps = speed_bps
        self.eta_sec = eta_sec
        self.stage = stage
    def to_dict(self) -> Dict[str, Any]:
        return {"downloaded_bytes": self.downloaded_bytes, "total_bytes": self.total_bytes, "speed_bps": self.speed_bps, "eta_sec": self.eta_sec, "stage": self.stage}
class SpeedMovingAverage:
    def __init__(self, window_size: int = 5):
        self.window_size = window_size
        self.samples: List[Tuple[float, int]] = []
    def add_sample(self, timestamp: float, byte_count: int):
        self.samples.append((timestamp, byte_count))
        if len(self.samples) > self.window_size:
            self.samples.pop(0)
