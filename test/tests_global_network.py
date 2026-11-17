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
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p
def _build_test_model(target_dir):
    import torch
    from safetensors.torch import save_file
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from transformers import PreTrainedTokenizerFast, Qwen2Config, Qwen2ForCausalLM
    os.makedirs(target_dir, exist_ok=True)
    vocab = {"<unk>": 0, "<|endoftext|>": 1, "hello": 2, "shard": 3, "network": 4, "global": 5, "cluster": 6}
