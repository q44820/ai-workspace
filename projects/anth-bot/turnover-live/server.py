"""Local-only API; starts HTTP service, NOT trading. No signing on boot or GET."""
import argparse,asyncio,hmac,json,os,secrets,time
from pathlib import Path
from aiohttp import web
import core
from core import Blocked,Controller,Config

def create_app(state,token,factory=None):
 if factory is None:
  from adapter import Venue
  factory=Venue
 controller=Controller(state,factory);app=web.Application(client_max_size=4096);csrf=secrets.token_urlsafe(32)
 import hashlib
 build=hashlib.sha256(b''.join((Path(__file__).parent/n).read_bytes() for n in ['core.py','adapter.py','server.py'])).hexdigest()[:16]
 app['controller']=controller
 pending=None
 def snapshot():
  d=controller.public();d.update(csrf=csrf,arbBusy=core.other_service_active(),serverTime=time.time(),build=build,startPending=pending is not None and not pending.done(),singleRound=controller.state.get('singleRound',False),roundsOpened=controller.state.get('roundsOpened',0));return d
 async def launch(config,resume,single_round):
  try:await controller.start(config,resume=resume,reset_stop=False,single_round=single_round)
  except Exception as e:
   if controller.state['phase'] not in {'rejected','needs_review'}:
    controller.diagnose(e,'preflight');controller.state.update(phase='rejected',message='本次启动未通过；未新增下单。'+controller.failure_label());controller.persist()
 async def handle(r):
  nonlocal pending
  if not hmac.compare_digest(r.headers.get('Authorization',''),'Bearer '+token):return web.json_response({'ok':False,'error':'无权限'},status=403)
  if r.method=='GET' and r.path=='/state':
   return web.json_response(snapshot(),headers={'Cache-Control':'no-store'})
  if r.method!='POST' or r.path not in {'/start','/stop'}:return web.json_response({'ok':False,'error':'无此接口'},status=404)
  if not hmac.compare_digest(r.headers.get('X-Turnover-CSRF',''),csrf):return web.json_response({'ok':False,'error':'页面已失效，请刷新'},status=403)
  try:
   d=await r.json()
   if not isinstance(d,dict):raise ValueError()
   if r.path=='/start':
    if not {'confirm','config'}<=set(d) or set(d)-{'confirm','config','continueSession','singleRound'} or d['confirm']!='用户手动启动本次真实合约小额试验':raise ValueError('必须手动确认真实合约风险')
    if 'singleRound' in d and not isinstance(d['singleRound'],bool):raise ValueError('单轮验收标记无效')
    if 'continueSession' in d and not isinstance(d['continueSession'],bool):raise ValueError('继续会话标记无效')
    if (pending is not None and not pending.done()) or controller.running:raise Blocked('已收到启动请求或正在运行；请查询状态，勿重复提交')
    if core.other_service_active():raise Blocked('原套利服务仍在运行/状态未知；禁止启动')
    Config.parse(d['config']);resume=d.get('continueSession') is True
    if resume and not controller.can_resume():raise Blocked('旧会话尚未核实，不可继续')
    if not resume and (controller.position or controller.state.get('session') or controller.state['journal'] or controller.state['phase']=='needs_review'):raise Blocked('已有旧会话；禁止重置预算')
    controller.stop.clear();pending=asyncio.create_task(launch(d['config'],resume,d.get('singleRound') is True));return web.json_response(snapshot(),status=202,headers={'Cache-Control':'no-store'})
   else:out=await controller.request_stop()
   return web.json_response(out,headers={'Cache-Control':'no-store'})
  except (Blocked,ValueError) as e:return web.json_response({'ok':False,'error':str(e) or '输入无效'},status=409)
  except Exception:
   return web.json_response({'ok':False,'error':controller.failure_label()+'；未自动重试，请刷新查看状态'},status=503)
 app.router.add_route('*','/{tail:.*}',handle)
 async def cleanup(app):
  if pending is not None and not pending.done():
   controller.stop.set()
   try:await asyncio.wait_for(asyncio.shield(pending),10)
   except Exception:pending.cancel();await asyncio.gather(pending,return_exceptions=True)
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
