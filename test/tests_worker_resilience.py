import asyncio
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
from fastapi.testclient import TestClient
from shard_engine import server, training
from shard_engine.distributed import CLUSTER, SCHEDULER, pipeline_coordinator, pipeline_proto as proto, pipeline_worker, resilience
def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p
def _build_sharded_qwen2(target_dir, hidden=32, layers=4):
    import torch
    from safetensors.torch import save_file
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from transformers import PreTrainedTokenizerFast, Qwen2Config, Qwen2ForCausalLM
    os.makedirs(target_dir, exist_ok=True)
    vocab = {"<unk>": 0, "<|endoftext|>": 1, "hello": 2, "shard": 3, "resilient": 4, "worker": 5, "alpha": 6, "beta": 7}
    tok_obj = Tokenizer(WordLevel(vocab=vocab, unk_token="<unk>"))
    tok_obj.pre_tokenizer = Whitespace()
    fast_tok = PreTrainedTokenizerFast(tokenizer_object=tok_obj, unk_token="<unk>", eos_token="<|endoftext|>", pad_token="<|endoftext|>")
    fast_tok.save_pretrained(target_dir)
    torch.manual_seed(7)
    cfg = Qwen2Config(vocab_size=len(vocab), hidden_size=hidden, intermediate_size=hidden * 2, num_hidden_layers=layers, num_attention_heads=4, num_key_value_heads=2, max_position_embeddings=128, tie_word_embeddings=False, eos_token_id=1, pad_token_id=1)
    cfg.save_pretrained(target_dir)
    mdl = Qwen2ForCausalLM(cfg).eval()
    with torch.no_grad():
