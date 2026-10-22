"""Launch the installed prototype without accessing the internet."""
import os
from pathlib import Path
import socket
import threading
import time
import webbrowser
import sys

ROOT = Path(__file__).resolve().parent


def main():
    if not (ROOT / "frontend/dist/index.html").is_file():
        raise SystemExit("Build the interface first: cd frontend && npm run build")
    os.environ["MEDIROUTE_OFFLINE"] = "1"
    sys.path.insert(0, str(ROOT / "backend"))
    import uvicorn

    # Bind before opening the browser, avoiding another service on the same port.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 8000))
    except OSError:
        sock.close()
        raise SystemExit("Port 8000 is in use. Stop the existing backend, then launch again.")
    server = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=8000))

    def open_when_ready():
        while not server.started and not server.should_exit:
            time.sleep(0.2)
        if server.started:
            webbrowser.open("http://127.0.0.1:8000")

    threading.Thread(target=open_when_ready, daemon=True).start()
    print("MediRoute offline: http://127.0.0.1:8000 — press Ctrl+C to stop.")
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()


if __name__ == "__main__":
    main()
