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

    # 1. We MUST let 'range' and 'accept-encoding' pass through for video to work.
    # We only exclude headers that break the proxy routing.
    excluded = {"host", "connection", "content-length"}
    headers = {key.lower(): value for key, value in request_headers.items() if key.lower() not in excluded}
    
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

    # 2. Only decompress HTML/JS. DO NOT decompress video chunks (.ts, .mp4) or it will corrupt them.
    if "gzip" in content_encoding and "text" in content_type:
        try: payload = gzip.decompress(payload)
        except Exception: pass
    elif "deflate" in content_encoding and "text" in content_type:
        try: payload = zlib.decompress(payload)
        except Exception: pass

    if "text/html" in content_type:
        try:
            text_payload = payload.decode("utf-8", errors="replace")
            text_payload = AD_SCRIPT.sub("", text_payload)
            
            # Inject crossorigin to bypass JS CORS blocks
            text_payload = re.sub(r'<script type="module"', '<script type="module" crossorigin', text_payload)
            
            # Stop Service Worker from crashing
            text_payload = text_payload.replace("navigator.serviceWorker.register('/service-worker.js');", "// SW disabled")
            
            payload = text_payload.encode("utf-8")
        except Exception:
            pass

    filtered_headers = {}
    # 3. Strip CSP and X-Frame so the iframe works
    excluded_response = {
        "content-length", "content-encoding", "transfer-encoding", "connection",
        "vary", "x-frame-options", "content-security-policy", "cross-origin-opener-policy",
        "cross-origin-embedder-policy", "cross-origin-resource-policy"
    }
    
    for key, value in response_headers.items():
        if key.lower() not in excluded_response:
            filtered_headers[key] = value
            
    # 4. Preserve Content-Range for video seeking
    if "Content-Range" in response_headers:
        filtered_headers["Content-Range"] = response_headers["Content-Range"]
        filtered_headers["Accept-Ranges"] = "bytes"
            
    # Only set Content-Length if it's not a 206 Partial Content request
    if status != 206:
        filtered_headers["Content-Length"] = str(len(payload))
    
    filtered_headers["Access-Control-Allow-Origin"] = "*"
    filtered_headers["Access-Control-Allow-Headers"] = "*"
    filtered_headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    filtered_headers["Access-Control-Expose-Headers"] = "Content-Range, Accept-Ranges, Content-Length"

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
        self.send_header("Access-Control-Expose-Headers", "Content-Range, Accept-Ranges")
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
