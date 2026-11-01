from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qsl, parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
import re
import gzip
import zlib
from urllib.parse import urljoin

ORIGIN = "https://cinejoy.pk"

def proxy_media_url(target_query, method, request_headers, body=None):
    params = parse_qs(target_query, keep_blank_values=True)
    target = params.get("url", [""])[0]
    if not target.startswith(("http://", "https://")):
        return 400, {"Content-Type": "text/plain; charset=utf-8"}, b"Invalid media URL"

    excluded = {
        "host", "connection", "content-length", "accept-encoding",
        "origin", "referer", "cookie",
    }
    headers = {
        key.lower(): value for key, value in request_headers.items()
        if key.lower() not in excluded
    }
    headers["referer"] = target
    headers["user-agent"] = "Mozilla/5.0"
    req = Request(target, data=body, headers=headers, method=method)

    try:
        response = urlopen(req, timeout=30)
        status = response.status
        payload = response.read()
        response_headers = response.headers
    except Exception as error:
        status = getattr(error, "code", 502)
        payload = getattr(error, "read", lambda: str(error).encode("utf-8"))()
        response_headers = getattr(error, "headers", {"Content-Type": "text/plain"})

    content_type = response_headers.get("Content-Type", "")
    if "gzip" in response_headers.get("Content-Encoding", "").lower() and "text" in content_type:
        try:
            payload = gzip.decompress(payload)
        except Exception:
            pass

    if (
        "mpegurl" in content_type.lower()
        or ".m3u8" in target.lower()
        or payload.lstrip().startswith(b"#EXTM3U")
    ):
        try:
            text = payload.decode("utf-8", errors="replace")
            lines = []
            host = request_headers.get("Host") or request_headers.get("host")
            forwarded = request_headers.get("X-Forwarded-Proto") or request_headers.get("x-forwarded-proto")
            scheme = "https" if forwarded == "https" else "http"
            media_base = (scheme + "://" + host) if host else ""
            for line in text.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    absolute = urljoin(target, stripped)
                    line = (media_base + "/_media_proxy?" + urlencode({"url": absolute})) if media_base else "/_media_proxy?" + urlencode({"url": absolute})
                elif "#EXT-X-KEY:" in line or "#EXT-X-MAP:" in line or "#EXT-X-MEDIA:" in line:
                    line = re.sub(
                        r'URI="([^"]+)"',
                        lambda match: 'URI="' + (media_base + "/_media_proxy?" if media_base else "/_media_proxy?") + urlencode({"url": urljoin(target, match.group(1))}) + '"',
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

AD_SCRIPT = re.compile(
    r"(?is)<script>\s*\(function \(\) \{\s*var adPref = localStorage\.getItem\(\"addies\"\).*?"
    r"\}\)\(\);\s*</script>"
)

def fetch_upstream(path, query, method, request_headers, body=None, embedded=False):
    path = "/" + path.lstrip("/")
    if path == "/_media_proxy":
        return proxy_media_url(query, method, request_headers, body)
    upstream_url = f"{ORIGIN}{path}"
    if query:
        upstream_url += "?" + query

    # 1. We MUST let 'range' pass through for video to work.
    # We only exclude headers that break the proxy routing.
    excluded = {"host", "connection", "content-length", "accept-encoding"}
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
            text_payload = text_payload.replace(
                "navigator.serviceWorker.register('/service-worker.js');",
                "void 0;",
            )
            if embedded:
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
                text_payload = text_payload.replace("<head>", "<head>" + startup, 1)

            # Inject crossorigin to bypass JS CORS blocks
            text_payload = re.sub(r'<script type="module"', '<script type="module" crossorigin', text_payload)

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
                path, urlencode(query_pairs), self.command, self.headers, body,
                embedded="1" in params.get("embed", []),
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
