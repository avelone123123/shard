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
