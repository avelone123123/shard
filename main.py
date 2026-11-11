import os
import sys
import threading
import time
import webbrowser
import uvicorn
from server import app
def _open_browser(url):
    time.sleep(1.2)
    try:
        webbrowser.open(url)
    except Exception:
        pass
if __name__ == "__main__":
    host = os.environ.get("SHARD_HOST", "0.0.0.0")
    port = int(os.environ.get("SHARD_PORT", "8000"))
    if not os.environ.get("SHARD_DOCKER"):
        bind_host = "127.0.0.1" if host == "0.0.0.0" else host
        threading.Thread(target=_open_browser, args=(f"http://{bind_host}:{port}",), daemon=True).start()
    uvicorn.run(app, host=host, port=port, log_level="info")