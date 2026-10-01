"""HydroAura 4D - single entry point.

    python main.py                 # backend + dashboard on http://localhost:8000
    python main.py --split         # backend :8000, dashboard served separately on :5173
    python main.py --headless      # run the closed loop without servers and print the event log
"""
from __future__ import annotations

import argparse
import functools
import http.server
import socketserver
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def serve_frontend(port: int) -> None:
    """Serve ./frontend with the stdlib so the dashboard can run on its own port."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT / "frontend"))
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("0.0.0.0", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


def main() -> None:
    ap = argparse.ArgumentParser(description="HydroAura 4D Pipeline & Digital Twin Platform")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000, help="backend (API + WebSocket) port")
    ap.add_argument("--split", action="store_true", help="serve the frontend on its own port")
    ap.add_argument("--frontend-port", type=int, default=5173)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--headless", action="store_true", help="no servers: run the mission and print the log")
    args = ap.parse_args()

    if args.headless:
        from tests.headless import run
        run()
        return

    import uvicorn

    url = f"http://localhost:{args.port}"
    if args.split:
        serve_frontend(args.frontend_port)
        url = f"http://localhost:{args.frontend_port}/?backend=localhost:{args.port}"
    print(f"\n  HydroAura 4D dashboard -> {url}\n  API docs               -> http://localhost:{args.port}/docs\n")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("backend.app:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
