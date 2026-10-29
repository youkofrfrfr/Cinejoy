from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qsl, parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
import re
import gzip
import zlib

ORIGIN = "https://cinejoy.pk"

AD_SCRIPT = re.compile(
    r"(?is)<script>\s*\(function \(\) \{\s*var adPref = localStorage\.getItem\(\"addies\"\).*?"
    r"\}\)\(\);\s*</script>"
)

def fetch_upstream(path, query, method, request_headers, body=None):
    path = "/" + path.lstrip("/")
    upstream_url = f"{ORIGIN}{path}"
    if query:
        upstream_url += "?" + query

    # 1. Strictly drop accept-encoding so Vercel doesn't choke on gzip
    excluded = {"host", "connection", "content-length", "accept-encoding"}
    headers = {key.lower(): value for key, value in request_headers.items() if key.lower() not in excluded}
    
    # 2. Fake Origin and Referer to bypass hotlink protection
    headers["referer"] = f"{ORIGIN}/"
    headers["origin"] = ORIGIN
    
    if "cookie" in request_headers:
        headers["cookie"] = request_headers["cookie"]

    req = Request(upstream_url, data=body, headers=headers, method=method)

    try:
        response = urlopen(req, timeout=15)
        status = response.status
        payload = response.read()
        response_headers = response.headers
    except Exception as error:
        status = getattr(error, 'code', 502)
        payload = getattr(error, 'read', lambda: str(error).encode('utf-8'))()
        response_headers = getattr(error, 'headers', {"Content-Type": "text/plain"})

    content_type = response_headers.get("Content-Type", "")
    content_encoding = response_headers.get("Content-Encoding", "").lower()

    # 3. Decompress manually if the server forced gzip
    if "gzip" in content_encoding:
        try: payload = gzip.decompress(payload)
        except Exception: pass
    elif "deflate" in content_encoding:
        try: payload = zlib.decompress(payload)
        except Exception: pass

    # 4. Strip ads and fix SvelteKit paths
    if "text/html" in content_type:
        try:
            text_payload = payload.decode("utf-8", errors="replace")
            text_payload = AD_SCRIPT.sub("", text_payload)
            
            # Keep the base tag so the scripts load from Cinejoy, NOT Vercel.
            # This stops the 502 error on JavaScript files.
            if "<head>" in text_payload:
                text_payload = text_payload.replace("<head>", f'<head><base href="{ORIGIN}/">')
            elif "<HEAD>" in text_payload:
                text_payload = text_payload.replace("<HEAD>", f'<HEAD><base href="{ORIGIN}/">')
                
            payload = text_payload.encode("utf-8")
        except Exception:
            pass

    filtered_headers = {}
    # 5. Nuke encoding and framing headers completely
    excluded_response = {
        "content-length", "content-encoding", "transfer-encoding", "connection",
        "vary", "x-frame-options", "content-security-policy", "cross-origin-opener-policy",
        "cross-origin-embedder-policy", "cross-origin-resource-policy"
    }
    
    for key, value in response_headers.items():
        if key.lower() not in excluded_response:
            filtered_headers[key] = value
            
    if status != 206:
        filtered_headers["Content-Length"] = str(len(payload))
    
    # 6. Allow CORS and embedded video
    filtered_headers["Access-Control-Allow-Origin"] = "*"
    filtered_headers["Access-Control-Allow-Headers"] = "*"
    filtered_headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"

    return status, filtered_headers, payload


class handler(BaseHTTPRequestHandler):
    def do_GET(self): self.proxy()
    def do_HEAD(self): self.proxy()
    def do_POST(self): self.proxy()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def proxy(self):
        try:
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
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length > 0 else None

            status, headers, payload = fetch_upstream(
                path, urlencode(query_pairs), self.command, self.headers, body
            )
            
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            
            if self.command != "HEAD":
                self.wfile.write(payload)
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(str(e).encode("utf-8"))
