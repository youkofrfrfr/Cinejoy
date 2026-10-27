from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener
import re

ORIGIN = "https://cinejoy.pk"
UPSTREAM_OPENER = build_opener(HTTPCookieProcessor(CookieJar()))
AD_SCRIPT = re.compile(
    r"(?is)<script>\s*\(function \(\) \{\s*var adPref = localStorage\.getItem\(\"addies\"\).*?"
    r"\}\)\(\);\s*</script>"
)
def fetch_upstream(path, query, method, request_headers, body=None, embedded=False):
    path = "/" + path.lstrip("/")
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
        payload = AD_SCRIPT.sub("", payload.decode("utf-8", errors="replace")).encode("utf-8")

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