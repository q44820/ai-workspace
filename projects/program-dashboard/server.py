"""Read-only program inventory. Bearer-path access; no account or process argv exposure."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import hmac
import json
import os
import subprocess
import threading
import time

from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent

def safe_web_url(value):
    if not isinstance(value,str) or any(ord(c)<33 or c=='\\' for c in value):
        return None
    try:
        parsed=urlsplit(value)
        if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password:
            return None
        _=parsed.port
        return value
    except ValueError:
        return None

SERVICE_MAP = {
    'basis-radar': ('user', 'basis-radar.service'),
    'arb-hub': ('system', 'arb-hub.service'),
    'anth-bot': ('system', 'anth-bot.service'),
    'program-dashboard': ('system', 'program-dashboard.service'),
}

def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def service_status(scope, unit):
    cmd = ['systemctl'] + (['--user'] if scope == 'user' else [])
    try:
        p = subprocess.run(cmd+['show', unit, '--property=LoadState,ActiveState,SubState,UnitFileState,Result'], capture_output=True, text=True, timeout=4)
        if p.returncode:
            return {'state':'unknown','label':'状态读取失败','detail':'只读检查失败，未执行修复'}
        fields = dict(line.split('=',1) for line in p.stdout.splitlines() if '=' in line)
        active = fields.get('ActiveState')
        if fields.get('LoadState') == 'not-found':
            return {'state':'stopped','label':'未安装','detail':'当前主机没有该服务登记'}
        if active == 'active':
            return {'state':'running','label':'运行中','detail':'服务进程运行；不代表接口、行情、订单或策略盈利已验证'}
        if active == 'failed':
            return {'state':'error','label':'服务异常','detail':'服务管理器报告失败，未自动重启'}
        if active == 'inactive':
            return {'state':'stopped','label':'已停止','detail':'服务当前未运行'}
        return {'state':'unknown','label':'状态变化中','detail':'检查时为启动/退出等过渡状态'}
    except (OSError, subprocess.TimeoutExpired):
        return {'state':'unknown','label':'待核验','detail':'未能在时限内读取服务状态'}

LOCAL_IDS = {'binance-alpha','binance-booster','lp-bncb-bnb','lp-price-range','lp-wallet-watchdog'}

class Inventory:
    def __init__(self, runtime, catalog=None):
        self.runtime = Path(runtime)
        self.catalog = catalog if catalog is not None else json.loads((HERE/'catalog.json').read_text())
        self.lock = threading.Lock()
        self.cached = None
        self.cached_at = 0

    def snapshot(self):
        with self.lock:
            if self.cached is not None and time.monotonic()-self.cached_at < 12:
                return self.cached
            items = []
            for source in self.catalog:
                row = dict(source)
                if row['id'] in SERVICE_MAP:
                    row.update(service_status(*SERVICE_MAP[row['id']]))
                    row['checked_at'] = utcnow()
                elif row['id'] in LOCAL_IDS:
                    row.update(state='unknown',label='运行状态待核验',detail='代码已在GitHub；尚无可用的精确运行状态，不能认定已停止',checked_at=utcnow())
                else:
                    row.update(state='manual', label='手动使用／代码归档', detail='代码已在GitHub；未绑定常驻服务，不代表现有服务已停止', checked_at=utcnow())
                items.append(row)
            sync = {'state':'missing','label':'本机状态尚未同步','age_seconds':None}
            try:
                local = json.loads((self.runtime/'local_snapshot.json').read_text())
                captured = datetime.fromisoformat(local['captured_at'])
                if captured.tzinfo is None:
                    raise ValueError('naive timestamp')
                age = max(0, int((datetime.now(timezone.utc)-captured).total_seconds()))
                stale = age > 900
                sync = {'state':'stale' if stale else 'fresh','label':'本机数据过期' if stale else '本机数据已同步','age_seconds':age,'captured_at':local['captured_at']}
                allowed = {'schedule','state','label','detail','checked_at'}
                by_id = {row['id']:row for row in items}
                for data in local.get('items',[]):
                    if not isinstance(data,dict) or data.get('id') not in LOCAL_IDS:
                        continue
                    row = by_id.get(data['id'])
                    if row is None:
                        continue
                    row.update({key:data[key] for key in allowed if key in data})
                    if stale:
                        row.update(state='unknown',label='状态已过期',detail='本机同步超过15分钟，不能把旧状态当当前状态')
            except (OSError, ValueError, TypeError, KeyError):
                pass
            try:
                web_links=json.loads((self.runtime/'web_links.json').read_text())
                if not isinstance(web_links,dict):web_links={}
            except (OSError,ValueError,TypeError):
                web_links={}
            for row in items:
                row.pop('web_url',None)
                # Self-link stays relative so a new private access URL is never stale.
                candidate='./' if row['id']=='program-dashboard' else safe_web_url(web_links.get(row['id']))
                if candidate:row['web_url']=candidate
            self.cached = {'generated_at':utcnow(),'read_only':True,'local_sync':sync,'items':items,'scope':'仅展示GitHub代码库中的项目；Alpha与Booster分别显示状态，不列未上传的旧任务、代理和系统服务。','privacy':'不展示密钥、账户、资产、持仓、日志、原始命令或认证配置；仅展示经确认的网页跳转入口。','status_note':'运行中仅表示进程存在；定时启用不等于任务成功；停止不等于代码不存在。'}
            self.cached_at = time.monotonic()
            return self.cached

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Never log bearer path or request details.

    def response(self, body, status=200, content_type='application/json; charset=utf-8', head=False, market_connect=False):
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        connect = "'self' https://api.hyperliquid.xyz https://mainnet.zklighter.elliot.ai" if market_connect else "'self'"
        self.send_header('Content-Security-Policy',f"default-src 'self'; script-src 'self'; style-src 'self'; connect-src {connect}; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def do_GET(self, head=False):
        path = self.path.split('?',1)[0]
        if path == '/health':
            return self.response(b'{"ok":true,"read_only":true}',head=head)
        parts = path.split('/')
        if len(parts)<4 or parts[1]!='v' or not hmac.compare_digest(hashlib.sha256(parts[2].encode()).hexdigest(), self.server.token_hash):
            return self.response(b'{"error":"not_found"}',404,head=head)
        suffix = '/'.join(parts[3:])
        if suffix.startswith('anth-live/'):
            name=suffix[len('anth-live/'):]
            if name=='api/status':
                try:
                    candidate=getattr(self.server,'live_status_file',self.server.inventory.runtime/'anth-status.json')
                    data=json.loads(candidate.read_text())
                    return self.response(json.dumps(data,ensure_ascii=False).encode(),head=head)
                except (OSError,ValueError,TypeError):
                    return self.response(b'{"ok":false,"err":"local status unavailable"}',503,head=head)
            assets={'':('japan.html','text/html; charset=utf-8'),'dashboard.css':('dashboard.css','text/css; charset=utf-8'),'dashboard.js':('dashboard.js','text/javascript; charset=utf-8'),'chart.umd.min.js':('chart.umd.min.js','text/javascript; charset=utf-8')}
            if name not in assets:return self.response(b'{"error":"not_found"}',404,head=head)
            filename,kind=assets[name];candidate=HERE.parent/'anth-bot'/'live-dashboard'/filename
            if not candidate.is_file():return self.response(b'{"error":"live_not_installed"}',503,head=head)
            return self.response(candidate.read_bytes(),content_type=kind,head=head)
        if suffix.startswith('anth-paper/'):
            name = suffix[len('anth-paper/'):]
            assets = {'':('paper.html','text/html; charset=utf-8'),'paper_engine.js':('paper_engine.js','text/javascript; charset=utf-8'),'paper_app.js':('paper_app.js','text/javascript; charset=utf-8'),'paper.css':('paper.css','text/css; charset=utf-8')}
            if name not in assets:
                return self.response(b'{"error":"not_found"}',404,head=head)
            filename,kind=assets[name]
            candidate=HERE.parent/'anth-bot'/filename
            if not candidate.is_file():
                return self.response(b'{"error":"paper_not_installed"}',503,head=head)
            return self.response(candidate.read_bytes(),content_type=kind,head=head,market_connect=True)
        if suffix == 'api/status':
            return self.response(json.dumps(self.server.inventory.snapshot(),ensure_ascii=False).encode(),head=head)
        assets = {'':'index.html','app.js':'app.js','style.css':'style.css'}
        if suffix not in assets:
            return self.response(b'{"error":"not_found"}',404,head=head)
        name = assets[suffix]
        types = {'index.html':'text/html; charset=utf-8','app.js':'text/javascript; charset=utf-8','style.css':'text/css; charset=utf-8'}
        return self.response((HERE/'static'/name).read_bytes(),content_type=types[name],head=head)

    def do_HEAD(self):
        self.do_GET(head=True)

    def deny_write(self):
        self.response(b'{"error":"read_only"}',405)
    do_POST = do_PUT = do_DELETE = do_PATCH = deny_write


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8902)
    parser.add_argument('--runtime',default=str(HERE/'runtime'))
    args = parser.parse_args()
    runtime = Path(args.runtime)
    token_hash = (runtime/'access.sha256').read_text().strip()
    if len(token_hash)!=64 or any(c not in '0123456789abcdef' for c in token_hash):
        raise SystemExit('ACCESS_HASH_REQUIRED')
    server = ThreadingHTTPServer((args.host,args.port),Handler)
    server.token_hash = token_hash
    server.inventory = Inventory(runtime)
    server.live_status_file=Path(os.environ.get('ANTH_LIVE_STATUS_FILE','/var/lib/anth-status-publisher/status.json'))
    server.serve_forever()

if __name__=='__main__':
    main()
