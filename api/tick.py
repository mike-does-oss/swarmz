"""The scheduled cloud run: Vercel Cron calls this every two hours (see vercel.json).

Only a caller that knows CRON_SECRET gets in; Vercel sends it as a bearer token. ?dry=1 says what
would run and changes nothing.
"""

import hmac
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        secret = os.environ.get("CRON_SECRET", "")
        given = self.headers.get("authorization", "")
        if not secret or not hmac.compare_digest(given, f"Bearer {secret}"):
            return self._send(401, {"error": "auth"})
        dry = parse_qs(urlparse(self.path).query).get("dry") == ["1"]
        lines = []

        def log(msg):
            lines.append(str(msg))
            print(msg, flush=True)

        try:
            from swarmz import cloud
            out = cloud.tick(run=not dry, log=log)
            self._send(200, {**out, "log": lines})
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": f"{type(e).__name__}: {str(e)[:300]}", "log": lines})

    def _send(self, status, body):
        data = json.dumps(body, default=str).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
