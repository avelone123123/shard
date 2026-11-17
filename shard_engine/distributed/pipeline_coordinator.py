import json
import os
import socket
import threading
import time
import uuid
try:
    import torch
    from transformers import AutoConfig, AutoTokenizer
    _TORCH_READY = True
except Exception:
    torch = None
    AutoConfig = None
    AutoTokenizer = None
    _TORCH_READY = False
from . import pipeline_proto as proto
from . import pipeline_worker as pw
def _parse_endpoint(ep):
