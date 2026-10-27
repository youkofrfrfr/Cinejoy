from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode, urlsplit
from proxy_core import fetch_upstream


class CinejoyHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        self.proxy_request()

    def do_HEAD(self):
        self.proxy_request()

    def do_POST(self):
        self.proxy_request()

    def proxy_request(self):
        request = urlsplit(self.path)
        params = dict(pair.split("=", 1) for pair in request.query.split("&") if "=" in pair)
        if request.path in {"/", "/index.html"} and params.get("embed") != "1":
            super().do_GET()
            return

        embedded = request.path == "/cinejoy" or (request.path == "/" and params.get("embed") == "1")
        upstream_path = "/sec.html" if embedded else request.path
        query_pairs = [(key, value) for key, value in params.items() if key != "embed"]
        body = None
        if self.command == "POST":
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))

        status, headers, payload = fetch_upstream(
            upstream_path.lstrip("/"),
            urlencode(query_pairs),
            self.command,
            self.headers,
            body,
            embedded=embedded,
        )
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(payload)
            except BrokenPipeError:
                pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 4173), CinejoyHandler).serve_forever()