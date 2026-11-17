import asyncio
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from fastapi.testclient import TestClient
from shard_engine import net_discovery, net_identity, net_strategy, net_tunnel, server, training
from shard_engine.distributed import CLUSTER, SCHEDULER, capabilities, pipeline_coordinator, pipeline_proto, pipeline_worker, planner
def _free_port():
