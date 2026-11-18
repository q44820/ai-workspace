"""Local-only API; starts HTTP service, NOT trading. No signing on boot or GET."""
import argparse,asyncio,hmac,json,os,secrets,time
from pathlib import Path
from aiohttp import web
from core import Blocked,Controller

def create_app(state,token,factory=None):
 if factory is None:
  from adapter import Venue
  factory=Venue
 controller=Controller(state,factory);app=web.Application(client_max_size=4096);csrf=secrets.token_urlsafe(32)
 import hashlib
 build=hashlib.sha256(b''.join((Path(__file__).parent/n).read_bytes() for n in ['core.py','adapter.py','server.py'])).hexdigest()[:16]
 app['controller']=controller
 async def handle(r):
  if not hmac.compare_digest(r.headers.get('Authorization',''),'Bearer '+token):return web.json_response({'ok':False,'error':'无权限'},status=403)
  if r.method=='GET' and r.path=='/state':
   from core import other_service_active
   d=controller.public();d.update(csrf=csrf,arbBusy=other_service_active(),serverTime=time.time(),build=build);return web.json_response(d,headers={'Cache-Control':'no-store'})
  if r.method!='POST' or r.path not in {'/start','/stop'}:return web.json_response({'ok':False,'error':'无此接口'},status=404)
  if not hmac.compare_digest(r.headers.get('X-Turnover-CSRF',''),csrf):return web.json_response({'ok':False,'error':'页面已失效，请刷新'},status=403)
  try:
   d=await r.json()
   if not isinstance(d,dict):raise ValueError()
   if r.path=='/start':
    if set(d)!={'confirm','config'} or d['confirm']!='用户手动启动本次真实合约小额试验':raise ValueError('必须手动确认真实合约风险')
    out=await controller.start(d['config'])
   else:out=await controller.request_stop()
   return web.json_response(out,headers={'Cache-Control':'no-store'})
  except (Blocked,ValueError) as e:return web.json_response({'ok':False,'error':str(e) or '输入无效'},status=409)
  except Exception:
   return web.json_response({'ok':False,'error':controller.failure_label()+'；未自动重试，请刷新查看状态'},status=503)
 app.router.add_route('*','/{tail:.*}',handle)
 async def cleanup(app):
  if controller.running and controller.task is not None:
   controller.stop.set()
   try:await asyncio.wait_for(asyncio.shield(controller.task),25)
   except Exception:pass
 app.on_cleanup.append(cleanup);return app

def main():
 p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8911);p.add_argument('--state',required=True);p.add_argument('--token-file',required=True);args=p.parse_args();token=Path(args.token_file).read_text().strip()
 if len(token)<32:raise SystemExit('API token missing')
 web.run_app(create_app(args.state,token),host='127.0.0.1',port=args.port,print=None,access_log=None)
if __name__=='__main__':main()
