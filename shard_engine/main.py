import os
import threading
import time
import webbrowser
import uvicorn
from . import process
from .server import app
def open_browser(url):
    time.sleep(1.5)
    try:
        webbrowser.open(url)
    except Exception:
        pass
def main(host=None, port=None, no_browser=None):
    bind_host = host or os.environ.get("SHARD_HOST", "0.0.0.0")
    bind_port = int(port or os.environ.get("SHARD_PORT", "8000"))
    skip_browser = no_browser if no_browser is not None else bool(
        os.environ.get("SHARD_DOCKER") or os.environ.get("SHARD_NO_BROWSER")
    )
    if not skip_browser:
        view_host = "127.0.0.1" if bind_host in ("0.0.0.0", "::", "") else bind_host
        threading.Thread(target=open_browser, args=(f"http://{view_host}:{bind_port}",), daemon=True).start()
    process.write_pid_info(os.getpid(), bind_host, bind_port)
    try:
        uvicorn.run(app, host=bind_host, port=bind_port, log_level="warning")
    finally:
        cur = process.read_pid_info()
        if cur and cur.get("pid") == os.getpid():
            process.clear_pid_info()
if __name__ == "__main__":
    main()

