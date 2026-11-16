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

HERE = Path(__file__).resolve().parent
SERVICE_MAP = {
    'basis-radar': ('user', 'basis-radar.service'),
    'basis-tunnel': ('user', 'basis-radar-tunnel.service'),
    'arb-hub': ('system', 'arb-hub.service'),
    'anth-bot': ('system', 'anth-bot.service'),
    'network-xray': ('system', 'xray.service'),
    'network-hysteria': ('system', 'hysteria-server.service'),
    'program-dashboard': ('system', 'program-dashboard.service'),
    'dashboard-tunnel': ('system', 'program-dashboard-tunnel.service'),
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
                else:
                    row.update(state='manual', label='手动使用／未部署', detail='代码已归档；没有此主机上的常驻服务证据', checked_at=utcnow())
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
                allowed = {'id','name','category','location','purpose','schedule','state','label','detail','code','usage','checked_at'}
                for data in local.get('items',[]):
                    if not isinstance(data,dict) or not {'id','name','state'}.issubset(data):
                        continue
                    row = {key:data[key] for key in allowed if key in data}
                    if stale:
                        row.update(state='unknown',label='状态已过期',detail='本机同步超过15分钟，不能把旧状态当当前状态')
                    items.append(row)
            except (OSError, ValueError, TypeError, KeyError):
                pass
            self.cached = {'generated_at':utcnow(),'read_only':True,'local_sync':sync,'items':items,'scope':'日本服务器已登记服务＋本机项目/LaunchAgent/OpenClaw与Hermes任务；不是全磁盘软件清单。','privacy':'不展示密钥、账户、资产、持仓、日志、原始命令、机器地址或认证配置。','status_note':'运行中仅表示进程存在；定时启用不等于任务成功；停止不等于代码不存在。'}
            self.cached_at = time.monotonic()
            return self.cached

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Never log bearer path or request details.

    def response(self, body, status=200, content_type='application/json; charset=utf-8', head=False):
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")
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
    server.serve_forever()

if __name__=='__main__':
    main()
