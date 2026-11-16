"""Local, read-only Chinese arbitrage observer. Python standard library only."""
import os
import official
from runtime import ScheduledCache, Scheduler, utcnow, zoned
import argparse
import csv
import io
import json
import threading
import gzip
import urllib.request
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME = Path(os.environ.get('ARB_HUB_RUNTIME', str(ROOT / 'runtime')))
IMPORTS = RUNTIME / 'imports'
CACHE = RUNTIME / 'cache'
FEED = 'https://arbtide.com/api/v1/pulse'
OWNED = set(official.URLS)  # Generic supported venues, not personal account ownership.
INTERVAL = 300
UNKNOWN = '未知'

def now():
    return datetime.now(timezone.utc).isoformat()

def beijing(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            return '来源原时间时区未知'
        return dt.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S 北京时间')
    except (ValueError, TypeError, AttributeError):
        return UNKNOWN

def parse_pulse(raw):
    data = raw.get('data', raw)
    prices = data.get('prices')
    if not isinstance(prices, list) or not prices:
        raise ValueError('公开 feed 缺少 prices')
    for row in prices:
        if not isinstance(row, dict) or not all(k in row for k in ('coin', 'price', 'fundingApr')):
            raise ValueError('价格行格式错误')
    stamp = data.get('updatedAt', raw.get('meta', {}).get('updatedAt'))
    if beijing(stamp) == UNKNOWN:
        raise ValueError('缺少有效更新时间')
    return {'prices': prices, 'best': data.get('arbitrage', {}).get('best'),
            'arbitrage_count': data.get('arbitrage', {}).get('routes', UNKNOWN),
            'funding_count': data.get('funding', {}).get('routes', UNKNOWN),
            'updatedAt': stamp, 'beijing': beijing(stamp), 'executable_routes': []}

def classify_html(html):
    return '公开页存在，但是 SPA/前端渲染，当前未自动接入' if any(s in html for s in ('id="root"', "id='root'", '正在加载', '<th')) else '未自动接入，需人工核实'

def canonical(platform):
    return 'Hyperliquid' if platform in ('HL', 'Hyperliquid（HL）') else platform

def route_key(row):
    return tuple(row.get(k, UNKNOWN) for k in ('coin', 'long_kind', 'short_kind', 'long_platform', 'short_platform', 'strategy', 'source_url', 'period', 'method', 'source_time', 'long_contract', 'short_contract'))

def deduplicate(rows):
    return list({route_key(r): r for r in rows}.values())

def normalize(row):
    if not isinstance(row, dict):
        raise ValueError('每行必须为对象')
    required = ('coin', 'strategy', 'long_platform', 'short_platform', 'long_kind', 'short_kind', 'source_url', 'source_time')
    if any(not isinstance(row.get(k), str) or not row[k].strip() for k in required):
        raise ValueError('缺少必要文本字段：' + ', '.join(required))
    if row['strategy'] not in ('funding', 'spread') or any(row[k] not in ('现货', '永续') for k in ('long_kind', 'short_kind')):
        raise ValueError('策略须为 funding/spread，合约腿须为现货/永续')
    if not row['source_url'].startswith(('https://', 'http://')):
        raise ValueError('来源链接须为 http(s)')
    if any(k in row for k in ('automatic', 'auto', 'last_success', 'last_attempt')) or row.get('status') not in (None, '', 'manual', 'snapshot'):
        raise ValueError('导入不得声称自动采集')
    clean = {k: str(row.get(k) or UNKNOWN) for k in (*required, 'period', 'cost', 'spread', 'rate', 'annual', 'method', 'long_contract', 'short_contract')}
    for k in ('long_platform', 'short_platform'):
        clean[k] = canonical(clean[k])
    clean.update(status='manual', label='手动离线导入，未自动接入、不会自动更新', collected_at=now())
    return clean

def parse_import(content, kind):
    if len(content.encode('utf-8')) > 1024 * 1024:
        raise ValueError('导入上限 1 MiB')
    rows = json.loads(content) if kind == 'json' else list(csv.DictReader(io.StringIO(content)))
    if not isinstance(rows, list) or len(rows) > 2000:
        raise ValueError('须为行数组，最多 2000 行')
    return deduplicate([normalize(r) for r in rows])

def filter_rows(rows, strategy='funding', search='', platform='', status='', owned=True):
    return [r for r in rows if r['strategy'] == strategy
            and (not owned or all(canonical(r[k]) in OWNED for k in ('long_platform', 'short_platform')))
            and (not search.strip() or search.strip().lower() == r['coin'].lower())
            and (not platform or platform in (r['long_platform'], r['short_platform']))
            and (not status or status == r['status'])]

class Cache(ScheduledCache):
    def __init__(self, path, clock=utcnow, interval=300, jitter=None):
        self.path = Path(path); self.lock = threading.RLock()
        try: self.state = json.loads(self.path.read_text())
        except (OSError, ValueError): self.state = {}
        self.setup(clock, interval, jitter)
    def view(self):
        with self.lock:
            state = {k: v for k, v in self.state.items() if k != 'raw'}
            state.update(self.status(self.state))
        state['field_notes']='公开 feed 仅摘要与计数，不是全站机会明细；fundingApr 单位未知'
        return state
    def refresh(self, fetch=None):
        def apply(raw):
            pulse=parse_pulse(raw); quote=zoned(pulse['updatedAt'])
            return dict(pulse=pulse,raw=raw,quote_time=quote.isoformat() if quote else None,
                provenance={'source':'ArbTide pulse','url':FEED,'fields':'摘要行情原值，fundingApr 单位未知；best 缺腿不可执行'})
        return self.run_refresh(fetch or fetch_pulse,apply)

def fetch_pulse():
    with urllib.request.urlopen(FEED, timeout=12) as response:
        if response.status != 200:
            raise ValueError(f'HTTP {response.status}')
        content = response.read(2 * 1024 * 1024 + 1)
        if len(content) > 2 * 1024 * 1024:
            raise ValueError('响应超过 2 MiB')
        return json.loads(content)

SOURCES = [
 ('ArbTide pulse', FEED, '唯一自动接入：进程启动及每约 5 分钟刷新。只提供摘要与计数。'),
 ('taoli.tools', 'https://taoli.tools/', '公开页存在，但是 SPA 壳，未自动接入'),
 ('CryptoLabs', 'https://cryptolabs.today/', '公开副本不含旧快照；仅支持本地手动导入，未自动采集'),
 ('Astro pulse', 'https://pulse.astro-btc.xyz/', 'SPA 壳，未自动接入；官方工作流入口外链'),
 ('ArbTide 状态', 'https://arbtide.com/zh/status', '仅外链；完整 API 需 key/credits，未调用；只自动采 pulse'),
 ('CoinGlass', 'https://www.coinglass.com/zh/FrArbitrage', '公开页有表头但静态 HTML 无机会行，未自动接入'),
 ('BRRR', 'https://terminal.brrr.capital/m', '目录可浏览；机会明细前端渲染，API Soon，未自动接入'),
 ('Humarb', 'https://humarb.app/t', '禁止采集，未接入：robots Disallow: /，先前 403，不绕过'),
 ('Astro funding', 'https://funding-v2.astro-btc.xyz/', 'SPA 壳，未自动接入'),
 ('QQQ', 'https://try.qqq.bot/', '前端壳 / Cloudflare 前置，未自动接入')]

def load_rows():
    # No production snapshot is published. Fresh checkout starts empty.
    rows = []
    manual = IMPORTS / 'manual.json'
    if manual.exists():
        rows.extend(json.loads(manual.read_text()))
    return deduplicate(rows)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def request_path(self):
        return urlparse(self.path).path

    def respond(self, payload, status=200, mime='application/json; charset=utf-8', cache='no-store'):
        body = payload.encode() if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False).encode()
        encoding = None
        accept = self.headers.get('Accept-Encoding', '')
        if 'gzip' in accept and len(body) >= 256:
            body = gzip.compress(body, compresslevel=6)
            encoding = 'gzip'
        self.send_response(status)
        self.send_header('Content-Type', mime)
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

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.request_path()
        if path == '/api/state':
            self.respond(state_view(self.server.cache, self.server.hub), cache='public, max-age=5')
        elif path in ('/', '/app.js', '/style.css'):
            name, mime = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}[path]
            self.respond((ROOT / 'static' / name).read_text(), mime=mime + '; charset=utf-8', cache='public, max-age=300')
        else:
            self.respond({'error': '未找到'}, 404)

    def do_POST(self):
        if os.environ.get("ARB_HUB_ALLOW_IMPORT") != "1":
            self.respond({"error": "只读模式：导入默认关闭"}, 403)
            return
        # Browsers must send same-origin JSON; cross-origin requests cannot write.
        origin = self.headers.get('Origin')
        if origin and origin != f'http://127.0.0.1:{self.server.server_port}':
            self.respond({'error': '仅允许本机同源导入'}, 403)
            return
        if self.path != '/api/import' or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            self.respond({'error': '仅支持 JSON 请求'}, 400)
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 2 * 1024 * 1024:
                raise ValueError('请求大小不合法')
            body = json.loads(self.rfile.read(size))
            if body.get('format') not in ('json', 'csv'):
                raise ValueError('格式须为 json/csv')
            rows = parse_import(body['content'], body['format'])
            path = IMPORTS / 'manual.json'
            with self.server.import_lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                existing = json.loads(path.read_text()) if path.exists() else []
                merged = deduplicate(existing + rows)
                temp = path.with_suffix('.tmp')
                temp.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
                temp.replace(path)
            self.respond({'imported': len(rows)})
        except (ValueError, KeyError, TypeError) as exc:
            self.respond({'error': str(exc)}, 400)

def state_view(cache=None, hub=None):
    cache = cache or Cache(CACHE / 'arbtide_pulse.json')
    hub = hub or official.Hub(CACHE)
    sources = [list(s) for s in SOURCES]
    sources[0][2] = '自动文档 feed 缓存；仅摘要，best 缺腿不可执行，计数不是本 app 明细数'
    sources[2][2] = '公开副本不含旧快照；本地手动导入，未自动采集'
    sources.append(['Bybit', 'https://api.bybit.com/v5/market/tickers?category=linear', '限制不可接：文档接口存在，本环境 403，未接入，不绕过'])
    return dict(cache=cache.view(), rows=load_rows(), sources=sources, **hub.view())

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8899)
    parser.add_argument('--bind', default='127.0.0.1', help='应用监听地址，默认仅本机回环')
    parser.add_argument('--public-port', type=int, default=0, help='可选的公网只读反代端口，反向代理到本机 --port，默认关闭')
    parser.add_argument('--quote-interval', type=float, default=30)
    parser.add_argument('--metadata-interval', type=float, default=900)
    parser.add_argument('--pulse-interval', type=float, default=300)
    args = parser.parse_args()
    if args.bind not in ('127.0.0.1', '::1') and not args.public_port:
        parser.error('应用进程只绑定回环；公网请使用 --public-port 反代，不要把应用直接绑到 0.0.0.0')
    if min(args.quote_interval, args.metadata_interval, args.pulse_interval) <= 0:
        parser.error('刷新周期必须大于零')
    cache = Cache(CACHE / 'arbtide_pulse.json', interval=args.pulse_interval)
    hub = official.Hub(CACHE, interval=args.quote_interval, metadata_interval=args.metadata_interval)
    stop = threading.Event()
    scheduler = Scheduler([*hub.caches.values(), cache])
    if args.bind not in ('127.0.0.1', '::1'):
        parser.error('应用进程只绑定回环')
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    server.cache, server.hub, server.import_lock = cache, hub, threading.Lock()
    thread = threading.Thread(target=scheduler.run, args=(stop,), daemon=True)
    thread.start()
    proxy = None
    proxy_thread = None
    if args.public_port:
        from public_proxy import PublicProxy
        proxy = PublicProxy(('0.0.0.0', args.public_port), ('127.0.0.1', args.port))
        proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        proxy_thread.start()
        print(f'只读反代：0.0.0.0:{args.public_port} → 127.0.0.1:{args.port}（仅 GET/HEAD 页面与 /api/state）')
    print(f'本机观察应用：http://127.0.0.1:{args.port}（Ctrl+C 退出）')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        if proxy is not None:
            proxy.shutdown(); proxy.server_close()
        thread.join(timeout=15)
        if proxy_thread is not None:
            proxy_thread.join(timeout=5)

if __name__ == '__main__':
    main()
