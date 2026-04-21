#!/usr/bin/env python3

from __future__ import annotations

import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DIST_DIR = Path(os.environ.get("FRONTEND_STATIC_DIR", ROOT_DIR / "web-ui" / "dist")).resolve()
HOST = os.environ.get("VITE_DEV_SERVER_HOST", "0.0.0.0")
PORT = int(os.environ.get("VITE_DEV_SERVER_PORT", "3001"))


class SpaHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST_DIR), **kwargs)

    def do_GET(self) -> None:
        request_path = self.path.split("?", 1)[0].split("#", 1)[0]
        candidate = (DIST_DIR / request_path.lstrip("/")).resolve()
        if request_path not in ("", "/") and not candidate.exists():
            self.path = "/index.html"
        return super().do_GET()


def main() -> None:
    if not DIST_DIR.exists():
        raise SystemExit(f"frontend dist directory not found: {DIST_DIR}")

    server = ThreadingHTTPServer((HOST, PORT), SpaHandler)
    print(f"Serving frontend dist from {DIST_DIR} at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()