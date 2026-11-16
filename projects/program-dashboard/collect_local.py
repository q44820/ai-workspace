"""Sanitized local inventory. Read-only inspection, no AI/model calls or job execution."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
import plistlib
import shlex
import sqlite3
import subprocess

LAUNCH_NAMES = {
 'com.openclaw.binance-alpha-monitor':('币安Alpha监控','跟踪Alpha项目、空投变化及提醒'),
 'com.openclaw.binance-booster-monitor':('币安Booster监控','跟踪Booster与币安公告变化'),
 'ai.openclaw.polymarket-daily-bet-summary.local':('Polymarket每日总结','汇总既有下注结果；看板不读取内容'),
 'ai.openclaw.polymarket-weather-summary.local':('天气策略总结','汇总天气策略历史结果'),
 'ai.openclaw.polymarket-linker.local':('Polymarket任务联动','旧任务间的调度联动'),
 'ai.openclaw.polymarket-position-check.local':('Polymarket持仓检查','旧账户检查任务；看板不查询账户'),
 'ai.openclaw.polymarket-shadow-v2-live.local':('Shadow V2实盘调度','历史实盘调度；这里只检查服务，不查订单，不启停'),
 'ai.openclaw.polymarket-shadow-v2.local':('Shadow V2本地模拟','历史本地模拟调度'),
 'com.openclaw.shadow-v2-100-watch':('Shadow V2累计提醒','达到模拟记录阈值后提醒'),
 'ai.openclaw.gateway':('OpenClaw网关','旧AI网关与任务通道'),
 'ai.hermes.gateway':('Hermes网关','当前AI聊天与任务通道'),
 'ai.openclaw.circuit-breaker.guard':('模型用量熔断守卫','观察模型用量并执行既有熔断规则'),
 'ai.openclaw.model-failover-local':('模型故障切换','旧模型故障检测与切换'),
 'ai.openclaw.cleanup':('本机定时清理','执行既有系统清理任务'),
 'com.crypto.wallet-monitor.local':('本机钱包监控','本地低频监控；不向看板发送钱包和仓位'),
 'ai.hermes.pearl-miner':('PEARL挖矿程序','历史挖矿程序；是否停用以系统登记为准'),
 'com.crypto.cpa.keep-online':('本机连接保活','既有连接保活服务'),
 'ai.qmd.embed':('本地知识索引','文档向量索引维护'),
 'ai.qmd.maintenance':('知识库维护','索引及检索维护'),
 'com.crypto.program-dashboard.sync':('程序看板本机同步','每5分钟同步脱敏状态；不调用大模型'),
}

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
  items.append(dict(id='local-service-'+hashlib.sha256(label.encode()).hexdigest()[:12],name=name,category='本机服务与定时',location='本机',purpose=purpose,schedule=schedule,state=state,label=title,detail=detail,usage='通过原程序/原任务使用；看板不提供启停按钮',checked_at=now()))
 return items,True

def openclaw_items(home):
 path=home/'.openclaw/state/openclaw.sqlite'
 if not path.exists():return [],False
 try:
  con=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=5)
  con.row_factory=sqlite3.Row
  rows=[dict(r) for r in con.execute('select name,enabled,schedule_kind,schedule_expr,every_ms,last_run_status,last_run_at_ms from cron_jobs order by sort_order')]
  con.close()
 except sqlite3.Error:return [],False
 items=[]
 for index,r in enumerate(rows):
  raw=r['name'];name=raw.split('【',1)[-1].split('】',1)[0] if '【' in raw and '】' in raw else raw
  # Only approved generic names; do not publish personalized schedule titles.
  if 'iota' in name.lower():name='历史代币定时检查'
  elif name=='memecoin-candidates-daily':name='Memecoin候选日报'
  elif '天气单盈亏总结' in name:name='天气策略盈亏总结'
  elif len(name)>70:name='旧定时任务'
  enabled=bool(r['enabled']);error=enabled and r['last_run_status'] in ('error','failed')
  state='error' if error else 'scheduled' if enabled else 'stopped'
  label='最近执行异常' if error else '定时已启用' if enabled else '已停用'
  detail='仅表示调度登记；任务可能使用AI，本站同步不调用AI。'
  if r['last_run_at_ms']:detail+=' 最近执行：'+str(safe_time(r['last_run_at_ms']))
  schedule=schedule_text({'kind':r['schedule_kind'],'expr':r['schedule_expr']})
  if r['schedule_kind']=='every':schedule='每'+str((r['every_ms'] or 0)//1000)+'秒'
  items.append(dict(id='openclaw-task-'+str(index),name=name,category='OpenClaw内部任务',location='本机',purpose='历史调度：'+name,schedule=schedule,state=state,label=label,detail=detail,usage='旧任务保留登记，停用项不要自动恢复',checked_at=now()))
 return items,True

def hermes_items(home):
 path=home/'.hermes/cron/jobs.json'
 try:
  d=json.loads(path.read_text());rows=d.get('jobs',[]) if isinstance(d,dict) else d
 except (OSError,ValueError):return [],False
 items=[]
 for index,r in enumerate(rows):
  raw=r.get('name','');name='群聊定时提取' if '聊天' in raw else '历史机票价格监控' if '票价' in raw else '历史购物提醒/监控' if any(x in raw for x in ['Luna','优惠','购买']) else 'Hermes登记任务'
  enabled=bool(r.get('enabled'));error=enabled and r.get('last_status') in ('error','failed')
  items.append(dict(id='hermes-task-'+str(index),name=name,category='Hermes内部任务',location='本机',purpose=name,schedule=schedule_text(r.get('schedule')),state='error' if error else 'scheduled' if enabled else 'stopped',label='最近执行异常' if error else '定时已启用' if enabled else '已停用',detail='仅同步启用状态与日期，不传提示词、错误正文或发送目标。最近执行：'+str(safe_time(r.get('last_run_at')) or '无记录'),usage='通过原任务管理器查看；不自动重开过期任务',checked_at=now()))
 return items,True

def collect(home=None):
 home=Path(home) if home else Path.home()
 items=[];sources={}
 for name,fn in [('launchagents',launch_items),('openclaw',openclaw_items),('hermes',hermes_items)]:
  rows,ok=fn(home);items+=rows;sources[name]=ok
 if not all(sources.values()):
  items.append(dict(id='local-inspection-warning',name='本机盘点有来源不可读',category='同步提示',location='本机',purpose='部分调度来源不可读，清单可能不完整',state='unknown',label='盘点不完整',detail='未自动修复或修改原调度',checked_at=now()))
 return {'captured_at':now(),'items':items}

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
