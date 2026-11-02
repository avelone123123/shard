import os
import sys
import threading
import time
import webbrowser
import uvicorn
from server import app
def _open_browser():
    time.sleep(1.2)
    try:
        webbrowser.open("http://localhost:8000")
    except Exception:
        pass
if __name__ == "__main__":
    host = os.environ.get("SHARD_HOST", "0.0.0.0")
    port = int(os.environ.get("SHARD_PORT", "8000"))
    if not os.environ.get("SHARD_DOCKER"):
        threading.Thread(target=_open_browser, daemon=True).start()
    uvicorn.run(app, host=host, port=port, log_level="info")