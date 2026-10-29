from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
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

    # Do not strip 'range' header (needed for video streaming)
    excluded = {"host", "connection", "content-length"}
    headers = {key.lower(): value for key, value in request_headers.items() if key.lower() not in excluded}
    
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
    except URLError as error:
        status = 502
        payload = str(error).encode("utf-8")
        response_headers = {"Content-Type": "text/plain; charset=utf-8"}

    content_type = response_headers.get("Content-Type", "")
    
    # Only modify HTML, not video chunks
    if "text/html" in content_type:
        try:
            text_payload = payload.decode("utf-8", errors="replace")
            text_payload = AD_SCRIPT.sub("", text_payload)
            
            # FIX: Inject base tag so SvelteKit assets load from Cinejoy, not your Vercel URL
            if "<head>" in text_payload:
                text_payload = text_payload.replace("<head>", f'<head><base href="{ORIGIN}/">')
            elif "<HEAD>" in text_payload:
                text_payload = text_payload.replace("<HEAD>", f'<HEAD><base href="{ORIGIN}/">')
                
            payload = text_payload.encode("utf-8")
        except Exception:
            pass

    filtered_headers = {}
    excluded_response = {
        "content-length", "content-encoding", "transfer-encoding", "connection",
        "vary", "x-frame-options"
    }
    
    for key, value in response_headers.items():
        if key.lower() not in excluded_response:
            filtered_headers[key] = value
            
    # If it's a 206 Partial Content (video seeking), let the original Content-Length pass
    if status != 206:
        filtered_headers["Content-Length"] = str(len(payload))
    
    # FIX: Allow all origins and media sources
    filtered_headers["Access-Control-Allow-Origin"] = "*"
    filtered_headers["Access-Control-Allow-Headers"] = "*"
    filtered_headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    filtered_headers["Content-Security-Policy"] = "default-src * 'unsafe-inline' 'unsafe-eval'; media-src * blob: data:;"

    return status, filtered_headers, payload
