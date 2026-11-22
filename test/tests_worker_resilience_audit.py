import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from fastapi.testclient import TestClient
from shard_engine import gguf_backend, server, training
from shard_engine.distributed import CLUSTER, SCHEDULER, pipeline_coordinator, pipeline_proto as proto, pipeline_worker, resilience
from test.tests_worker_resilience import _ScriptedPeerServer, _build_sharded_qwen2
def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p
def _parse_openai_sse(raw_text):
    tokens = []
    errors = []
    finish_reasons = []
    saw_done = False
    for block in raw_text.split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        data_parts = [ln[5:].strip() for ln in lines if ln.startswith("data:")]
        if not data_parts:
            continue
        payload = "\n".join(data_parts)
        if payload == "[DONE]":
