import json
import logging
import os
import threading
import time
import uuid
from collections import deque
log = logging.getLogger("shard.resilience")
HEALTHY = "HEALTHY"
SUSPECT = "SUSPECT"
FAILED = "FAILED"
RECOVERING = "RECOVERING"
_DEFAULTS = {
    "heartbeat_interval_s": 2.4,
    "suspect_missed": 2,
    "dead_missed": 5,
    "monitor_tick_s": 1.0,
    "connect_timeout_s": 5.0,
    "load_timeout_s": 300.0,
    "prefill_timeout_s": 180.0,
    "decode_step_timeout_s": 30.0,
    "release_timeout_s": 2.0,
    "poll_slice_s": 0.25,
    "slow_step_warn_s": 10.0,
