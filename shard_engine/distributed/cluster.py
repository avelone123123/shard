import copy
import os
import secrets
import socket
import threading
import time
import uuid
from .. import gguf_backend as _gguf
from .. import hardware as _hardware
from .. import lan as _lan
from . import capabilities as _cap
from . import pipeline_coordinator as _pcoord
from . import pipeline_worker as _pworker
from . import planner as _planner
from . import scheduler as _sched
HEARTBEAT_TIMEOUT_S = 12.0
WORKER_TOKEN_PREFIX = "sk-shard-wrk-"
def _free_port():
