from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener
from pathlib import Path
import re

ORIGIN = "https://cinejoy.pk"
UPSTREAM_OPENER = build_opener(HTTPCookieProcessor(CookieJar()))
AD_SCRIPT = re.compile(
    r"(?is)<script>\s*\(function \(\) \{\s*var adPref = localStorage\.getItem\(\"addies\"\).*?"
    r"\}\)\(\);\s*</script>"
)

LOCAL_ASSETS = Path(__file__).resolve().parent / "_app" / "immutable"


def load_local_asset(path):
    asset = LOCAL_ASSETS / path.removeprefix("/_app/immutable/").lstrip("/")
    try:
        return asset.read_bytes()
    except (FileNotFoundError, IsADirectoryError, OSError):
        return None


def fetch_upstream(path, query, method, request_headers, body=None, embedded=False):
    path = "/" + path.lstrip("/")
    if method in {"GET", "HEAD"} and path.startswith("/_app/immutable/"):
        payload = load_local_asset(path)
        if payload is not None:
            return 200, {
                "Content-Type": "application/javascript; charset=utf-8",
                "Content-Length": str(len(payload)),
                "Cache-Control": "public, max-age=31536000, immutable",
                "X-Content-Type-Options": "nosniff",
            }, payload

    upstream_url = f"{ORIGIN}{path}"
    if query:
        upstream_url += "?" + query

    excluded = {"host", "connection", "content-length", "origin", "referer", "cookie", "accept-encoding"}
    headers = {key: value for key, value in request_headers.items() if key.lower() not in excluded}
    request = Request(upstream_url, data=body, headers=headers, method=method)

    try:
        response = UPSTREAM_OPENER.open(request, timeout=15)
        status = response.status
        payload = response.read()
        response_headers = response.headers
    except HTTPError as error:
        status = error.code
        payload = error.read()
        response_headers = error.headers
    except URLError as error:
        status = 502
        payload = str(error).encode("utf-8")
        response_headers = {"Content-Type": "text/plain; charset=utf-8"}

    content_type = response_headers.get("Content-Type", "")
    if "text/html" in content_type:
        text = AD_SCRIPT.sub("", payload.decode("utf-8", errors="replace"))
        if embedded:
            text = text.replace("navigator.serviceWorker.register('/service-worker.js');", "void 0;")
            startup = '''<script>
history.replaceState(null, '', '/?embed=1');
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.getRegistrations().then(registrations =>
    Promise.all(registrations.map(registration => registration.unregister()))
  ).catch(() => {});
}
if ('caches' in window) {
  caches.keys().then(names =>
    Promise.all(names.map(name => caches.delete(name)))
  ).catch(() => {});
}
</script>'''
            text = text.replace("<head>", "<head>" + startup, 1)
        payload = text.encode("utf-8")

    filtered_headers = {}
    excluded_response = {
        "content-length", "content-encoding", "transfer-encoding", "connection",
        "vary", "set-cookie", "x-frame-options", "content-security-policy",
    }
    for key, value in response_headers.items():
        if key.lower() not in excluded_response:
            filtered_headers[key] = value
    filtered_headers["Content-Length"] = str(len(payload))
    filtered_headers["X-Content-Type-Options"] = "nosniff"
    return status, filtered_headers, payload