"""Loopback-to-public read-only reverse proxy. Standard library only.

Allows only GET/HEAD for /, /app.js, /style.css, /api/state.
Never forwards POST, import, admin, or arbitrary URLs.
"""
import gzip
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ALLOWED = frozenset({'/', '/app.js', '/style.css', '/api/state'})
# Per-IP: 60 requests / 60s is enough for 30s polling plus page assets.
WINDOW = 60
LIMIT = 60


class RateLimiter:
    def __init__(self, window=WINDOW, limit=LIMIT):
        self.window = window
        self.limit = limit
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, ip):
        now = time.monotonic()
        with self._lock:
            q = self._hits[ip]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


class PublicProxy(ThreadingHTTPServer):
    def __init__(self, listen, backend):
        self.backend = backend
        self.limiter = RateLimiter()
        super().__init__(listen, ProxyHandler)


class ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _path(self):
        return urlparse(self.path).path

    def _deny(self, status, message):
        body = ('{"error":"%s"}' % message).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def do_POST(self):
        self._deny(403, '只读：不接受写入或导入')

    def do_PUT(self):
        self._deny(403, '只读：不接受写入或导入')

    def do_DELETE(self):
        self._deny(403, '只读：不接受写入或导入')

    def do_HEAD(self):
        self._forward()

    def do_GET(self):
        self._forward()

    def _forward(self):
        path = self._path()
        if path not in ALLOWED:
            self._deny(404, '未找到')
            return
        ip = self.client_address[0]
        if not self.server.limiter.allow(ip):
            self._deny(429, '请求过于频繁')
            return
        host, port = self.server.backend
        url = f'http://{host}:{port}{path}'
        # Always GET the loopback app so HEAD can advertise Content-Length.
        # Bypass HTTP_PROXY; loopback must stay local.
        req = urllib.request.Request(url, method='GET', headers={'Accept-Encoding': 'identity'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=12) as resp:
                raw = resp.read()
                status = resp.status
                ctype = resp.headers.get('Content-Type', 'application/octet-stream')
                cache = resp.headers.get('Cache-Control', 'no-store')
                already = resp.headers.get('Content-Encoding', '')
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
            ctype = exc.headers.get('Content-Type', 'application/json; charset=utf-8') if exc.headers else 'application/json; charset=utf-8'
            cache = 'no-store'
            already = exc.headers.get('Content-Encoding', '') if exc.headers else ''
        except Exception:
            self._deny(502, '上游不可用')
            return
        body = raw
        encoding = already or None
        accept = self.headers.get('Accept-Encoding', '')
        if 'gzip' in accept and not encoding and len(body) >= 256:
            body = gzip.compress(body, compresslevel=6)
            encoding = 'gzip'
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        if encoding:
            self.send_header('Content-Encoding', encoding)
        self.send_header('Cache-Control', cache)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)
