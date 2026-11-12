import importlib
import sys
if __name__ == "__main__":
    from shard_engine.main import main
    main()
else:
    sys.modules[__name__] = importlib.import_module("shard_engine.main")
