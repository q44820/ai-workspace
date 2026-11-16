"""Sanitized local inventory. Read-only inspection, no AI/model calls or job execution."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import json
import os
import plistlib
import shlex
import subprocess

LAUNCH_NAMES = {
 'com.openclaw.binance-alpha-monitor':('币安Alpha监控','跟踪Alpha项目、空投变化及提醒'),
 'com.openclaw.binance-booster-monitor':('币安Booster监控','跟踪Booster与币安公告变化'),
}
PROJECT_IDS = {'com.openclaw.binance-alpha-monitor':'binance-alpha', 'com.openclaw.binance-booster-monitor':'binance-booster'}

def now():
 return datetime.now(timezone.utc).isoformat(timespec='seconds')

def safe_time(value):
 try:
  if isinstance(value,(int,float)):return datetime.fromtimestamp(value/1000,timezone.utc).isoformat(timespec='seconds')
  return datetime.fromisoformat(value).astimezone(timezone.utc).isoformat(timespec='seconds') if value else None
 except (ValueError,TypeError,OverflowError):return None

def schedule_text(data):
 if isinstance(data,dict):
  if data.get('kind')=='every':return '周期任务'
  if data.get('kind')=='once':return '一次性任务'
  expr=str(data.get('expr',''))
  if expr and len(expr)<=100 and all(c in '0123456789*/, -?' for c in expr):return 'cron '+expr
 return '按原任务计划'

def launch_items(home):
 try:
  text=subprocess.check_output(['launchctl','list'],text=True,timeout=10)
 except (OSError,subprocess.SubprocessError):return [],False
 loaded={}
 for line in text.splitlines()[1:]:
  p=line.split()
  if len(p)==3:loaded[p[2]]=p
 items=[]
 for file in (home/'Library/LaunchAgents').glob('*.plist'):
  label=file.stem
  if label not in LAUNCH_NAMES:continue
  warning=False
  try:
   b=file.read_bytes()
   try:d=plistlib.loads(b)
   except Exception:
    d=plistlib.loads(b[:b.index(b'</plist>')+8]);warning=True
  except (OSError,ValueError,plistlib.InvalidFileException):
   d={};warning=True
  name,purpose=LAUNCH_NAMES[label]
  process=loaded.get(label)
  scheduled=bool(d.get('StartInterval') or d.get('StartCalendarInterval'))
  if not process:state,title,detail='stopped','已停止／未加载','有配置，但当前未加载；未自动恢复'
  elif process[0]!='-':state,title,detail='running','运行中','进程存在，未验证业务接口或通知送达'
  elif process[1] not in ('0','-'):state,title,detail='error','上次执行异常','调度已加载，最近退出码非零；不是已验证成功'
  elif scheduled:state,title,detail='scheduled','定时已启用','当前等待触发，不能把等待状态当已停止'
  else:state,title,detail='unknown','已加载待核验','当前没有进程，非定时服务需另核验'
  if warning:detail+='；配置格式异常，仅只读提取'
  interval=d.get('StartInterval')
  schedule='每'+str(interval)+'秒' if isinstance(interval,int) else ('日历定时' if d.get('StartCalendarInterval') else '常驻／按需')
  items.append(dict(id=PROJECT_IDS[label],name=name,category='本机服务与定时',location='本机',purpose=purpose,schedule=schedule,state=state,label=title,detail=detail,usage='通过原程序/原任务使用；看板不提供启停按钮',checked_at=now()))
 return items,True

def collect(home=None):
 home=Path(home) if home else Path.home()
 items,ok=launch_items(home)
 return {'captured_at':now(),'items':items,'complete':ok,'scope':'github-projects-only'}

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--upload-config');args=parser.parse_args()
 snapshot=collect();target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
 temp=target.with_suffix('.new');temp.write_text(json.dumps(snapshot,ensure_ascii=False));temp.chmod(0o600);temp.replace(target)
 if args.upload_config:
  config=json.loads(Path(args.upload_config).read_text())
  # Host and SSH runtime settings stay in this private local file, never in Git or the site.
  env=dict(os.environ);env.update(config.get('env',{}))
  remote="""import json,sys,os
from pathlib import Path
p=Path.home()/'program-dashboard/runtime/local_snapshot.json'
b=sys.stdin.buffer.read(262145)
if len(b)>262144:raise SystemExit(2)
d=json.loads(b)
if not isinstance(d.get('items'),list) or len(d['items'])>300:raise SystemExit(3)
t=p.with_suffix('.new');t.write_bytes(b);t.chmod(0o600);os.replace(t,p)
"""
  command=config['ssh']+['python3 -c '+shlex.quote(remote)]
  result=subprocess.run(command,input=target.read_bytes(),env=env,capture_output=True,timeout=45)
  if result.returncode:raise SystemExit('STATUS_UPLOAD_FAILED')
 print('SANITIZED_STATUS_SYNC_OK items='+str(len(snapshot['items'])))

if __name__=='__main__':main()
