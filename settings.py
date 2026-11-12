import importlib
import sys
sys.modules[__name__] = importlib.import_module("shard_engine.settings")
