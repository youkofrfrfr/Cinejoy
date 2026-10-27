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
PLAYER_FALLBACK_SCRIPT = r'''<script>
(() => {
  const showFallback = () => {
    if (document.querySelector('[data-playback-fallback]')) return;
    const overlay = document.createElement('section');
    overlay.dataset.playbackFallback = '';
    overlay.style.cssText = 'position:fixed;inset:0;z-index:2147483647;display:grid;place-items:center;padding:24px;background:rgba(0,0,0,.92);color:#fff;font:16px/1.5 system-ui,sans-serif;text-align:center';
    overlay.innerHTML = '<div style="max-width:440px"><h2 style="font-size:24px;margin:0 0 12px">This source did not start</h2><p style="margin:0 0 20px;color:#d1d5db">The selected video source is unavailable. Try another title or reload the player.</p><button data-retry style="margin:4px;padding:10px 16px;cursor:pointer">Retry</button><button data-browse style="margin:4px;padding:10px 16px;cursor:pointer">Browse titles</button></div>';
    overlay.querySelector('[data-retry]').addEventListener('click', () => location.reload());
    overlay.querySelector('[data-browse]').addEventListener('click', () => location.assign('/?embed=1'));
    document.body.appendChild(overlay);
  };
  const watch = (video) => {
    if (video.dataset.fallbackWatched) return;
    video.dataset.fallbackWatched = 'true';
    video.addEventListener('error', showFallback, {once:true});
    video.addEventListener('playing', () => {
      clearTimeout(video.playbackFallbackTimer);
      document.querySelector('[data-playback-fallback]')?.remove();
    });
    video.playbackFallbackTimer = setTimeout(() => {
      if (video.readyState < 2) showFallback();
    }, 20000);
  };
  new MutationObserver(() => document.querySelectorAll('video').forEach(watch))
    .observe(document, {subtree:true, childList:true});
  document.querySelectorAll('video').forEach(watch);
})();
</script>'''


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
        text = AD_SCRIPT.sub("", payload.decode("utf-8", errors="replace"))
        if embedded:
            text = text.replace(
                "<head>",
                "<head><script>history.replaceState(null, '', '/?embed=1')</script>"
                + PLAYER_FALLBACK_SCRIPT,
                1,
            )
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