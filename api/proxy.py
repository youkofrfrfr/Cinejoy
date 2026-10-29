from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qsl, parse_qs, urlencode, urlsplit
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener
import re
import gzip
import zlib

ORIGIN = "https://cinejoy.pk"
UPSTREAM_OPENER = build_opener(HTTPCookieProcessor(CookieJar()))

AD_SCRIPT = re.compile(
    r"(?is)<script>\s*\(function \(\) \{\s*var adPref = localStorage\.getItem\(\"addies\"\).*?"
    r"\}\)\(\);\s*</script>"
)

def fetch_upstream(path, query, method, request_headers, body=None):
    path = "/" + path.lstrip("/")
    upstream_url = f"{ORIGIN}{path}"
    if query:
        upstream_url += "?" + query

    # 1. Drop accept-encoding so we receive plain text, not gzipped binary
    excluded = {"host", "connection", "content-length", "accept-encoding"}
    headers = {key.lower(): value for key, value in request_headers.items() if key.lower() not in excluded}
    
    # 2. Force Origin and Referer so Cinejoy/CDN allows the connection
    headers["referer"] = f"{ORIGIN}/"
    headers["origin"] = ORIGIN
    
    if "cookie" in request_headers:
        headers["cookie"] = request_headers["cookie"]

    req = Request(upstream_url, data=body, headers=headers, method=method)

    try:
        response = UPSTREAM_OPENER.open(req, timeout=15)
        status = response.status
        payload = response.read()
        response_headers = response.headers
    except HTTPError as error:
        status = error.code
        payload = error.read()
        response_headers = error.headers
    except Exception as error:
        status = 502
        payload = str(error).encode("utf-8")
        response_headers = {"Content-Type": "text/plain; charset=utf-8"}

    content_type = response_headers.get("Content-Type", "")
    content_encoding = response_headers.get("Content-Encoding", "").lower()

    # 3. Manually decompress if upstream ignored our headers and sent gzip
    if "gzip" in content_encoding:
        try:
            payload = gzip.decompress(payload)
        except Exception:
            pass
    elif "deflate" in content_encoding:
        try:
            payload = zlib.decompress(payload)
        except Exception:
            pass

    # 4. Strip ads and fix SvelteKit asset paths
    if "text/html" in content_type:
        try:
            text_payload = payload.decode("utf-8", errors="replace")
            text_payload = AD_SCRIPT.sub("", text_payload)
            
            # Inject <base> tag so JS/CSS assets load correctly
            if "<head>" in text_payload:
                text_payload = text_payload.replace("<head>", f'<head><base href="{ORIGIN}/">')
            elif "<HEAD>" in text_payload:
                text_payload = text_payload.replace("<HEAD>", f'<HEAD><base href="{ORIGIN}/">')
                
            payload = text_payload.encode("utf-8")
        except Exception:
            pass

    filtered_headers = {}
    # 5. Strip encoding headers so Vercel doesn't try to re-encode
    excluded_response = {
        "content-length", "content-encoding", "transfer-encoding", "connection",
        "vary", "x-frame-options", "content-security-policy"
    }
    
    for key, value in response_headers.items():
        if key.lower() not in excluded_response:
            filtered_headers[key] = value
            
    # Only set content length if it's not a 206 Partial Content (video stream)
    if status != 206:
        filtered_headers["Content-Length"] = str(len(payload))
    
    # 6. Allow embedding and video playback
    filtered_headers["Access-Control-Allow-Origin"] = "*"
    filtered_headers["Access-Control-Allow-Headers"] = "*"
    filtered_headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"

    return status, filtered_headers, payload

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.proxy()

    def do_HEAD(self):
        self.proxy()

    def do_POST(self):
        self.proxy()

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
                path,
                urlencode(query_pairs),
                self.command,
                self.headers,
                body,
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
