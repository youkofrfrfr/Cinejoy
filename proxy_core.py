from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin
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


def proxy_media_url(upstream_url, query, method, request_headers, body=None):
    params = parse_qs(query, keep_blank_values=True)
    target = params.get("url", [""])[0]
    if not target.startswith(("http://", "https://")):
        return 400, {"Content-Type": "text/plain; charset=utf-8"}, b"Invalid media URL"

    excluded = {
        "host", "connection", "content-length", "origin", "referer",
        "cookie", "accept-encoding",
    }
    headers = {
        key: value for key, value in request_headers.items()
        if key.lower() not in excluded
    }
    headers["User-Agent"] = "Mozilla/5.0"
    headers["Referer"] = target
    request = Request(target, data=body, headers=headers, method=method)

    try:
        response = UPSTREAM_OPENER.open(request, timeout=30)
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
    is_playlist = (
        "mpegurl" in content_type.lower()
        or ".m3u8" in target.lower()
        or payload.lstrip().startswith(b"#EXTM3U")
    )
    if is_playlist:
        try:
            text = payload.decode("utf-8", errors="replace")
            lines = []
            host = request_headers.get("Host") or request_headers.get("host") or "localhost"
            scheme = "https" if request_headers.get("X-Forwarded-Proto") == "https" else "http"
            media_base = scheme + "://" + host
            for line in text.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    absolute = urljoin(target, stripped)
                    media_query = urlencode({"url": absolute})
                    line = media_base + "/_media_proxy?" + media_query
                elif "#EXT-X-KEY:" in line or "#EXT-X-MAP:" in line or "#EXT-X-MEDIA:" in line:
                    line = re.sub(
                        r'URI="([^"]+)"',
                        lambda match: 'URI="' + media_base + "/_media_proxy?" + urlencode({"url": urljoin(target, match.group(1))}) + '"',
                        line,
                    )
                lines.append(line)
            payload = "\n".join(lines).encode("utf-8")
            content_type = "application/vnd.apple.mpegurl"
        except Exception:
            pass

    if (
        not payload.lstrip().startswith(b"#EXTM3U")
        and re.search(r"/(?:1080p|720p|480p)/", target, re.IGNORECASE)
        and re.search(r"\.(?:jpg|jpeg|png|mp4)$", target, re.IGNORECASE)
    ):
        content_type = "video/mp4"

    filtered = {
        "Content-Type": content_type or "application/octet-stream",
        "Content-Length": str(len(payload)),
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-store",
    }
    for key in ("Content-Range", "Accept-Ranges", "Content-Disposition", "ETag", "Last-Modified"):
        if response_headers.get(key):
            filtered[key] = response_headers.get(key)
    return status, filtered, payload


def fetch_upstream(path, query, method, request_headers, body=None, embedded=False):
    path = "/" + path.lstrip("/")
    if path == "/_media_proxy":
        return proxy_media_url(path, query, method, request_headers, body)
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
(function () {
  function mediaProxy(value) {
    try {
      var url = typeof value === 'string' ? value : (value && value.url ? value.url : String(value || ''));
      if (/^https?:\\/\\//i.test(url)
          && (\\.m3u8(?:$|\\?)|\\.mpd(?:$|\\?))/i.test(url)
          && new URL(url, location.href).origin !== location.origin) {
        return '/_media_proxy?' + new URLSearchParams({url: url});
      }
    } catch (_) {}
    return value;
  }
  var originalFetch = window.fetch;
  if (originalFetch) {
    window.fetch = function (input, init) {
      var value = typeof input === 'string' ? input : (input && input.url ? input.url : input);
      var proxied = mediaProxy(value);
      if (typeof input !== 'string' && input && input.url && proxied !== value) {
        input = new Request(proxied, input);
      } else if (typeof input === 'string') {
        input = proxied;
      }
      return originalFetch.call(window, input, init);
    };
  }
  var originalXHROpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url) {
    var args = Array.prototype.slice.call(arguments);
    args[1] = mediaProxy(url);
    return originalXHROpen.apply(this, args);
  };
  function rewriteMediaAttributes(root) {
    root.querySelectorAll && root.querySelectorAll('video[src], source[src], track[src]').forEach(function (element) {
      var src = element.getAttribute('src');
      var proxied = mediaProxy(src);
      if (proxied !== src) element.setAttribute('src', proxied);
    });
  }
  rewriteMediaAttributes(document);
  new MutationObserver(function (mutations) {
    mutations.forEach(function (mutation) {
      rewriteMediaAttributes(mutation.target);
      mutation.addedNodes.forEach(function (node) {
        if (node.nodeType === 1) rewriteMediaAttributes(node);
      });
    });
  }).observe(document.documentElement, {subtree: true, childList: true, attributes: true, attributeFilter: ['src']});
})();
(function () {
  var storageKey = 'cinejoy-error-logs-v1';
  var originalError = console.error ? console.error.bind(console) : function () {};
  function record(type, message) {
    try {
      var entries = JSON.parse(localStorage.getItem(storageKey) || '[]');
      entries.push({
        time: new Date().toISOString(),
        type: String(type),
        message: String(message || '').slice(0, 4000)
      });
      if (entries.length > 200) entries = entries.slice(-200);
      localStorage.setItem(storageKey, JSON.stringify(entries));
    } catch (_) {}
  }
  console.error = function () {
    record('console.error', Array.from(arguments).map(function (value) {
      try { return typeof value === 'string' ? value : JSON.stringify(value); }
      catch (_) { return String(value); }
    }).join(' '));
    return originalError.apply(console, arguments);
  };
  window.addEventListener('error', function (event) {
    record('page.error', (event.message || 'Unknown error') + (event.filename ? ' at ' + event.filename + ':' + event.lineno + ':' + event.colno : '') + (event.error && event.error.stack ? '\\n' + event.error.stack : ''));
  });
  window.addEventListener('unhandledrejection', function (event) {
    var reason = event.reason && event.reason.stack ? event.reason.stack : (event.reason && event.reason.message ? event.reason.message : String(event.reason || 'Unhandled promise rejection'));
    record('unhandledrejection', reason);
  });
  window.addEventListener('load', function () {
    record('startup', 'Page loaded: ' + location.href + ' | ' + navigator.userAgent);
  }, { once: true });
  var originalFetch = window.fetch;
  if (originalFetch) {
    window.fetch = function () {
      var url = arguments[0] && typeof arguments[0] === 'object' && arguments[0].url ? arguments[0].url : String(arguments[0] || '');
      return originalFetch.apply(this, arguments).then(function (response) {
        if (response.status >= 400) record('http.error', response.status + ' ' + response.statusText + ' ' + url);
        return response;
      }).catch(function (error) {
        record('network.error', url + ' - ' + (error && error.message ? error.message : String(error)));
        throw error;
      });
    };
  }
  var originalXHROpen = XMLHttpRequest.prototype.open;
  var originalXHRSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__cinejoyUrl = String(url || '');
    return originalXHROpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function () {
    this.addEventListener('load', function () {
      if (this.status >= 400) record('http.error', this.status + ' ' + this.__cinejoyUrl);
    });
    this.addEventListener('error', function () {
      record('network.error', this.__cinejoyUrl || 'XMLHttpRequest failed');
    });
    return originalXHRSend.apply(this, arguments);
  };
})();
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