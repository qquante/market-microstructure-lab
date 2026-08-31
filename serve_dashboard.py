#!/usr/bin/env python3
from __future__ import annotations

import argparse
import http.server
import json
import socketserver
import urllib.parse
from pathlib import Path

from bulk_redis import get_json


ROOT = Path(__file__).resolve().parent
DASHBOARD = ROOT / "dashboard"


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DASHBOARD), **kwargs)

    def send_head(self):
        requested = DASHBOARD / self.path.lstrip("/").split("?", 1)[0]
        if self.path.startswith("/token/") or self.path == "/":
            self.path = "/index.html"
        elif not requested.exists() and "." not in requested.name:
            self.path = "/index.html"
        return super().send_head()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/live_data.json":
            payload = self.redis_live_payload()
            if payload is not None:
                data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
        return super().do_GET()

    def redis_live_payload(self):
        try:
            return get_json()
        except Exception as exc:
            print(f"redis live_data fallback: {exc}")
            return None

    def log_message(self, format, *args):
        print(f"{self.address_string()} - {format % args}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the BULK Strategy Lab dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    with socketserver.TCPServer((args.host, args.port), DashboardHandler) as httpd:
        print(f"Dashboard: http://{args.host}:{args.port}")
        httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
