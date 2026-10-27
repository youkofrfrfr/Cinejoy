from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qsl, parse_qs, urlencode, urlsplit
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from proxy_core import fetch_upstream


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.proxy()

    def do_HEAD(self):
        self.proxy()

    def do_POST(self):
        self.proxy()

    def proxy(self):
        params = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        path = params.get("path", [""])[0]
        if not path or path.startswith(("//", "http:", "https:")):
            self.send_error(400, "A relative app path is required")
            return

        query_pairs = [
            (key, value)
            for key, value in parse_qsl(urlsplit(self.path).query, keep_blank_values=True)
            if key not in {"path", "embed"}
        ]
        body = None
        if self.command == "POST":
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))

        status, headers, payload = fetch_upstream(
            path,
            urlencode(query_pairs),
            self.command,
            self.headers,
            body,
            embedded="1" in params.get("embed", []),
        )
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)


if __name__ == "__main__":
    from http.server import HTTPServer

    HTTPServer(("127.0.0.1", 8000), handler).serve_forever()