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
    print("=" * 60)
    print("  SHARD // LOCAL HUGGING FACE RUNNER & OPENAI SERVER")
    print("=" * 60)
    print("  Dashboard:        http://localhost:8000")
    print("  OpenAI Base URL:  http://localhost:8000/v1")
    print("  Default API Key:  sk-shard-local-avelone-dev")
    print("=" * 60)
    threading.Thread(target=_open_browser, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")