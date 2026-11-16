#!/usr/bin/env python3
"""Local BNCB/BNB 30-minute range checks, WxPusher only, zero model calls."""
import argparse,datetime,fcntl,json,os,shutil,subprocess,time,urllib.request
from pathlib import Path
R=Path(__file__).resolve().parent.parent;O=R/'output/bncb_lp_monitor';WX=R/'scripts/wxpusher_config.json'
THRESHOLD=.02;INTERVAL=1800

def save(p,d):
 p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix('.tmp');t.write_text(json.dumps(d,ensure_ascii=False,indent=2));os.replace(t,p)
def read(p):return json.loads(p.read_text()) if p.exists() else {}
def classify(price,lower,upper):
 if not 0<lower<upper or price<=0:raise ValueError('INVALID_PRICE_RANGE')
 dl=(price-lower)/lower;du=(upper-price)/upper
 status='below' if price<lower else 'above' if price>=upper else 'near_both' if dl<=THRESHOLD+1e-12 and du<=THRESHOLD+1e-12 else 'near_lower' if dl<=THRESHOLD+1e-12 else 'near_upper' if du<=THRESHOLD+1e-12 else 'safe'
 return {'status':status,'distanceLowerPct':100*dl,'distanceUpperPct':100*du}
def snapshot():
 node=shutil.which('node') or '/opt/homebrew/bin/node';r=subprocess.run([node,str(R/'scripts/bncb_lp_snapshot.cjs')],capture_output=True,text=True,timeout=155)
 if r.returncode:raise RuntimeError('CHAIN_SNAPSHOT_FAILED')
 wallet=os.environ.get('LP_WALLET','').strip()
 if not wallet:raise RuntimeError('LP_WALLET_REQUIRED')
 d=json.loads(r.stdout);assert d['readOnly'] and d['wallet'].lower()==wallet.lower()
 for row in d['positions']:row.update(classify(row['ratio'],row['lowerRatio'],row['upperRatio']))
 save(O/'latest.json',d);return d
def message(d,setup=False):
 title='BNCB/BNB区间监控脚本已部署' if setup else 'BNCB/BNB LP区间预警'
 lines=[title,'钱包：由运行时配置提供，不在提醒中显示地址','每30分钟检查；距离任一边界≤2%或已越界才提醒；正常区间静默。','纯本地脚本，不调用大模型、不自动交易。','区间判断按BNCB/BNB相对价格；以下折U上下限会随BNB价格变化。']
 names={'safe':'区间内，安全','near_lower':'接近下限','near_upper':'接近上限','near_both':'同时接近上下限','below':'已跌出下限','above':'已涨出上限'}
 for r in d['positions']:
  if not setup and r['status']=='safe':continue
  lines += ['',f"仓位 #{r['id']}｜{names[r['status']]}",f"BNCB现价：{r['priceU']:.8f} U",f"区间：{r['lowerU']:.8f} ～ {r['upperU']:.8f} U",f"距下限：{r['distanceLowerPct']:.2f}%｜距上限：{r['distanceUpperPct']:.2f}%"]
 if not d['positions']:lines.append('当前没有有效BNCB/BNB仓位，已退出仓位不会继续提醒。')
 lines += ['',f"链上检查时间：{d['checkedAt']}",'30分钟采样不能保证在快速行情中提前提醒。']
 return title,'\n'.join(lines)
def send(title,content,kind):
 c=read(WX);uids=c.get('uids');assert c.get('app_token') and isinstance(uids,list) and len(uids)==1,'WXPUSHER_CONFIG'
 rec={'kind':kind,'createdAt':datetime.datetime.now().astimezone().isoformat(),'status':'sending','summary':title};path=O/('setup-receipt.json' if kind=='setup' else 'last-notification.json');save(path,rec)
 payload={'appToken':c['app_token'],'uids':uids,'topicIds':[],'summary':title,'content':content,'contentType':1,'url':'https://pancakeswap.finance/liquidity'}
 req=urllib.request.Request('https://wxpusher.zjiecode.com/api/send/message',data=json.dumps(payload,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'},method='POST')
 try:
  op=urllib.request.build_opener(urllib.request.ProxyHandler({}));res=json.load(op.open(req,timeout=25));targets=res.get('data') or [];targets=[targets] if isinstance(targets,dict) else targets
  rec.update(apiCode=res.get('code'),targetCodes=[x.get('code') for x in targets],messageContentIds=[x.get('messageContentId') for x in targets if x.get('messageContentId')],messageIds=[x.get('messageId') for x in targets if x.get('messageId')]);ok=res.get('code')==1000 and bool(targets) and all(x.get('code')==1000 for x in targets);rec['status']='accepted' if ok else 'rejected';save(path,rec)
  if not ok:raise RuntimeError('WXPUSHER_REJECTED')
  # The provider retired delivery-status queries. Acceptance is not device delivery.
  return rec
 except Exception:
  if rec['status']=='sending':rec['status']='uncertain';save(path,rec)
  raise RuntimeError('WXPUSHER_SEND_NOT_CONFIRMED') from None

def once(setup=False,dry=False):
 O.mkdir(parents=True,exist_ok=True)
 with (O/'monitor.lock').open('a') as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return {'status':'already_running'}
  s=read(O/'state.json');now=time.time()
  try:
   d=snapshot();warnings=[r for r in d['positions'] if r['status']!='safe'];s.update(lastCheckAt=d['checkedAt'],lastSuccessAt=d['checkedAt'],positionIds=[r['id'] for r in d['positions']],error=None)
   if setup:
    old=read(O/'setup-receipt.json')
    if old.get('status') in ['accepted','sending','uncertain']:return {'status':'setup_deduplicated','snapshot':d}
   should=setup or bool(warnings) and now-s.get('lastAlertAt',0)>=INTERVAL-5
   if should and not dry:
    if not setup:s['lastAlertAt']=now;save(O/'state.json',s)
    title,body=message(d,setup);send(title,body,'setup' if setup else 'boundary')
   save(O/'state.json',s);return {'status':'checked','warningCount':len(warnings),'notificationAttempted':should and not dry,'snapshot':d}
  except Exception as e:
   s.update(lastFailureAt=datetime.datetime.now().astimezone().isoformat(),error=type(e).__name__+':'+str(e)[:90]);save(O/'state.json',s)
   if not setup and not dry and now-s.get('lastErrorAlertAt',0)>=7200:
    s['lastErrorAlertAt']=now;save(O/'state.json',s)
    try:send('BNCB/BNB监控异常','本轮链上读取或推送失败，无法判断是否临近区间。请勿把无提醒理解为安全。下一轮30分钟后重试；同类异常最多2小时提醒一次。','error')
    except Exception:pass
   raise

def main():
 a=argparse.ArgumentParser();a.add_argument('--once',action='store_true');a.add_argument('--setup',action='store_true');a.add_argument('--dry-run',action='store_true');a.add_argument('--daemon',action='store_true');args=a.parse_args()
 if not args.daemon:print(json.dumps(once(args.setup,args.dry_run),ensure_ascii=False));return
 while True:
  start=time.time()
  try:once()
  except Exception:pass
  time.sleep(max(1,INTERVAL-(time.time()-start)))
if __name__=='__main__':main()
