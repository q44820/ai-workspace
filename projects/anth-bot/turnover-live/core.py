"""Small, user-started live turnover controller. Importing never signs or sends orders.
Only trade-ID-confirmed fills count. Any unknown outcome stops resubmission.
This module never reads secrets; SDK adapter is created only after explicit HTTP Start.
"""
import asyncio,json,math,secrets,subprocess,time
from typing import Any
from dataclasses import asdict,dataclass
from decimal import Decimal,ROUND_DOWN
from pathlib import Path

class Blocked(RuntimeError):pass

def number(v):return not isinstance(v,bool) and isinstance(v,(int,float)) and math.isfinite(v)
@dataclass(frozen=True)
class Config:
 target:float=1000
 round_cap:float=50
 loss:float=3
 @classmethod
 def parse(cls,d):
  if not isinstance(d,dict) or set(d)-{'target','round_cap','loss'}:raise ValueError('参数字段无效')
  x=cls(**d)
  for k,top in [('target',1000),('round_cap',50),('loss',3)]:
   v=getattr(x,k)
   if not number(v) or v<=0 or v>top:raise ValueError('超过本次授权上限或参数无效')
  return x

def size_for(c,io,lt,di,dl):
 if not all(number(v) and v>0 for v in [io,lt]):raise ValueError('无有效报价')
 dec=min(di,dl);step=Decimal(10)**-dec
 # 30bp pre-submission price margin prevents a nominal 50U limit using stale prices.
 q=float((Decimal(str(c.round_cap))/Decimal(str((io+lt)*1.003))).quantize(step,rounding=ROUND_DOWN))
 if q<=0 or min(q*io,q*lt)<10:raise ValueError('低于交易所最小名义额，跳过而非放大仓位')
 return q

def vwap(rows,q):
 left=q;n=0
 for p,z in rows:
  if not all(number(v) and v>0 for v in [p,z]):raise Blocked('盘口数据无效')
  take=min(left,z);n+=take*p;left-=take
  if left<1e-10:return n/q
 raise Blocked('盘口深度不足')

def tick(p,buy):
 from decimal import ROUND_CEILING
 return float((Decimal(str(p))/Decimal('.1')).to_integral_value(rounding=ROUND_DOWN if buy else ROUND_CEILING)*Decimal('.1'))

def entry(b,c,di,dl):
 a=[]
 for direction in ['S','L']:
  sign=1 if direction=='S' else -1;p=b['io']['asks' if sign==1 else 'bids'][0][0]
  q=size_for(c,p,b['lt']['asks' if sign==1 else 'bids'][0][0],di,dl)
  li=vwap(b['lt']['asks' if sign==1 else 'bids'],q)*(1+sign*.0001)
  lo=vwap(b['lt']['bids' if sign==1 else 'asks'],q)*(1-sign*.0001)
  io=vwap(b['io']['asks' if sign==1 else 'bids'],q)*(1+sign*.0001)
  net=q*sign*(p-io+lo-li)-q*(p*.0001+io*.0003+(li+lo)*.00028)
  cost=max(0,-net/(q*p)*10000)
  if cost<=20 and net/(q*p)*10000>-30:a.append(dict(dir=direction,price=tick(p,direction=='L'),qty=q,costBp=cost))
 return min(a,key=lambda x:x['costBp']) if a else None

def exit_price(b,p,relaxed):
 sign=1 if p['dir']=='S' else -1;buy=sign==1;q=p['qty']
 lt=vwap(b['lt']['bids' if buy else 'asks'],q)*(1-sign*.0001)
 target=-(q*p['io'])*.0012 if relaxed else 0
 bound=(sign*(p['io']+lt-p['lt'])-p['fees']/q-lt*.00028-target/q)/(sign+.0001)
 top=b['io']['bids' if buy else 'asks'][0][0]
 return tick(min(top,bound) if buy else max(top,bound),buy)

def stop_reason(c,total,equity):
 if not number(equity):return 'accounting_unknown'
 if equity<=-c.loss:return 'loss'
 if total>=c.target:return 'volume'
 return None

def other_service_active():
 try:p=subprocess.run(['systemctl','is-active','anth-bot'],capture_output=True,text=True,timeout=3)
 except (OSError,subprocess.TimeoutExpired):return True
 # Unknown also blocks: never treat permission/network errors as account ownership.
 if p.stdout.strip() in {'inactive','failed'} and p.returncode in {0,3}:return False
 return True

class Ledger:
 def __init__(self,rows=()):
  self.rows=[];self.ids=set()
  for x in rows:self.add(x)
 def add(self,x):
  if x.get('venue') not in {'io','lt'} or not isinstance(x.get('id'),str) or not x['id']:raise Blocked('无交易所成交标识')
  if x.get('side') not in {-1,1} or not all(number(x.get(k)) for k in ['qty','price','fee','pnl']) or min(x['qty'],x['price'])<=0:raise Blocked('成交费用或盈亏未确认；不按估算值计实盘')
  key=(x['venue'],x['id'])
  if key in self.ids:
   if next(r for r in self.rows if (r['venue'],r['id'])==key)!=x:raise Blocked('同成交标识数据冲突')
   return
  self.ids.add(key);self.rows.append(dict(x))
 @property
 def total(self):return sum(x['qty']*x['price'] for x in self.rows)
 @property
 def net(self):return sum(x['pnl']-x['fee'] for x in self.rows)
 @property
 def fees(self):return sum(x['fee'] for x in self.rows)
 def qty(self,leg):return round(sum(x['side']*x['qty'] for x in self.rows if x['venue']==leg),9)
 def side_volume(self,leg):return sum(x['qty']*x['price'] for x in self.rows if x['venue']==leg)

class Controller:
 def __init__(self,path,factory):
  self.venue:Any=None;self.position:Any=None
  try:asyncio.get_event_loop()
  except RuntimeError:asyncio.set_event_loop(asyncio.new_event_loop())
  self.path=Path(path);self.factory=factory;self.config=Config();self.running=False;self.stop=asyncio.Event();self.task=None;self.mutex=asyncio.Lock();self.market=None;self.funding=None
  self.state={'phase':'stopped','journal':[],'fills':[],'config':asdict(self.config),'session':None,'message':'默认停止；本页不会自动下单'}
  if self.path.exists():
   self.state=json.loads(self.path.read_text());self.config=Config.parse(self.state.get('config',{}));self.state['phase']='needs_review' if self.state.get('phase') not in {'stopped','finished','rejected'} else self.state['phase'];self.state['message']='重启后不自动恢复；保留原订单日志和风险，需核对交易所'
  self.ledger=Ledger(self.state.get('fills',[]))
  self.position=self.state.get('position')
 def persist(self):
  self.state.update(config=asdict(self.config),fills=self.ledger.rows,position=self.position,updated=time.time())
  self.path.parent.mkdir(parents=True,exist_ok=True);p=self.path.with_suffix('.new');p.write_text(json.dumps(self.state,ensure_ascii=False));p.chmod(0o600);p.replace(self.path)
 def public(self):
  return {'ok':True,'running':self.running,'phase':self.state['phase'],'message':self.state.get('message'),'config':asdict(self.config),'session':self.state.get('session'),'started':self.state.get('started'),'updated':self.state.get('updated'),'market':self.market,'position':self.position,'volume':{'io':self.ledger.side_volume('io'),'lt':self.ledger.side_volume('lt'),'both':self.ledger.total},'realizedNetExFunding':self.ledger.net,'fees':self.ledger.fees,'funding':self.funding,'riskEquity':self.state.get('riskEquity'),'orders':[{'venue':i['venue'],'purpose':i['purpose'],'outcome':i['outcome'],'qty':i['qty'],'side':i['side'],'created':i['created']} for i in self.state['journal'][-30:]],'fills':self.ledger.rows[-100:],'limitsNote':'两所开平累计1000U停止开新仓；每轮双边开仓合计≤50U。3U是触发线不是损失保证；收尾可超量。真实费用按交易所返回，未知不记0。'}
 async def preflight(self):
  if other_service_active():raise Blocked('原套利服务仍运行或状态未知，共用账户禁止启动；本程序不会停止原服务')
  if self.state['phase']=='needs_review' or any(x['outcome']=='unknown' for x in self.state['journal']):raise Blocked('有未核实的旧订单；禁止重发或新开仓')
  v=self.factory();v.min_free=self.config.round_cap/2+1
  try:
   await v.initialize();await v.preflight();b=await v.book();entry(b,self.config,v.io_dec,v.lt_dec)
  except BaseException:
   await v.close();raise
  return v
 async def start(self,d):
  async with self.mutex:
   if self.running:raise Blocked('已有会话运行，禁止重复启动')
   if self.position or self.state.get('session') or self.state['phase']=='needs_review' or any(i['outcome']=='unknown' for i in self.state['journal']):raise Blocked('本实例有旧会话/未核实订单；不得重置预算或重复授权')
   self.config=Config.parse(d);self.stop.clear();self.state['phase']='preflight';self.persist()
   try:
    self.venue=await self.preflight()
    if self.stop.is_set():
     await self.venue.close();self.venue=None;raise Blocked('预检期间用户已请求停止，未启动交易')
   except BaseException:
    self.state['phase']='rejected';self.state['message']='启动预检未通过；未发送订单';self.persist();raise
   self.stop.clear();self.running=True;self.state.update(phase='warmup',session=secrets.token_hex(12),started=time.time(),message='已由用户手动启动；60秒真实行情预热');self.persist();self.task=asyncio.create_task(self.run());return self.public()
 async def request_stop(self):
  self.stop.set();self.state['message']='用户停止：撤本程序挂单并收尾自己的仓位，不清除历史';self.persist();return self.public()
 async def execute(self,leg,side,qty,price,purpose,maker=False):
  if purpose=='open' and leg=='io' and self.stop.is_set():raise Blocked('用户已停止，禁止新增首腿')
  if other_service_active():raise Blocked('原套利服务运行或未知，禁止订单提交')
  if any(x['outcome']=='unknown' for x in self.state['journal']):raise Blocked('未知订单存在，禁止重发')
  i=dict(venue=leg,side=side,qty=qty,price=price,purpose=purpose,reduce=purpose!='open',client=secrets.token_hex(16) if leg=='io' else secrets.randbelow(2**47-1)+1,outcome='unknown',created=time.time())
  self.state['journal'].append(i);self.persist() # WAL before submission; crashes retain ambiguous intent
  try:
   ack=await self.venue.submit(i,maker=maker)
   result=await self.venue.settle(i,ack,self.stop)
   if not result.get('terminal'):raise Blocked('订单终态未确认')
   for f in result['fills']:self.ledger.add(f)
   i['outcome']='terminal';i['filled']=sum(f['qty'] for f in result['fills']);self.persist();return result['fills']
  except BaseException:
   self.state.update(phase='needs_review',message='订单结果或费用未知；禁止自动重发，需交易所核实');self.persist();raise Blocked('订单提交/核验未知；不得重发') from None
 async def verify(self):
  # Account replicas may lag confirmed trade IDs. Retry READS only, never resubmit.
  for attempt in range(4):
   p=await self.venue.exposure()
   if all(number(p.get(k)) and abs(p[k]-self.ledger.qty(k))<=1e-8 for k in ['io','lt']):return p
   if attempt<3:await asyncio.sleep(.25)
  raise Blocked('实际仓位与本程序成交账不一致；可能有外部交易，冻结，禁止误平')
 async def refresh(self):
  if other_service_active():raise Blocked('原套利服务运行/未知，账户隔离失效；冻结，不自动停止原服务或平共享仓')
  b=await self.venue.book();self.market={'at':b['at'],'ioBid':b['io']['bids'][0][0],'ioAsk':b['io']['asks'][0][0],'ltBid':b['lt']['bids'][0][0],'ltAsk':b['lt']['asks'][0][0]}
  await self.verify();self.funding=await self.venue.funding(self.state['started']);unreal=0
  if self.position:
   p=self.position;sgn=1 if p['dir']=='S' else -1
   io=vwap(b['io']['asks' if sgn==1 else 'bids'],p['qty']);lt=vwap(b['lt']['bids' if sgn==1 else 'asks'],p['qty']);unreal=p['qty']*sgn*(p['io']-io+lt-p['lt'])-p['qty']*(io*.0003+lt*.00028)
  # Unknown funding halts opening; never call it 0 and claim 3U includes it.
  if self.funding is None:raise Blocked('实际资金费未核实，禁止继续开仓')
  self.state['riskEquity']=self.ledger.net+self.funding+unreal;self.persist();return b
 async def pair(self,b,a,closing=False,force=False):
  if closing:
   p=self.position;side=1 if p['dir']=='S' else -1;q=p['qty'];price=vwap(b['io']['asks' if side==1 else 'bids'],q) if force else exit_price(b,p,time.time()-p['opened']>=60)
  else:
   side=1 if a['dir']=='L' else -1;q=a['qty'];price=a['price']
  before=self.ledger.net
  fs=await self.execute('io',side,q,price,'close' if closing else 'open',maker=not force)
  filled=sum(x['qty'] for x in fs)
  if not filled:return False
  io=sum(x['qty']*x['price'] for x in fs)/filled
  await self.verify() # Position-only reads never determine order finality.
  if not closing:
   self.position={'dir':a['dir'],'qty':filled,'io':io,'lt':None,'opened':time.time(),'fees':sum(x['fee'] for x in fs),'unhedged':True};self.persist()
  # No intentional 500ms delay in live hedging. Re-read book and hedge exactly confirmed IO fills.
  hb=await self.venue.book();ltlevels=hb['lt']['bids' if side==1 else 'asks'];lp=vwap(ltlevels,filled)
  if not closing and filled*(io+lp*1.003)>self.config.round_cap+1e-8:raise Blocked('实际开仓上限受价格跳变影响；保留风险并冻结，不增大对冲量')
  ls=await self.execute('lt',-side,filled,lp,'close' if closing else 'open')
  lq=sum(x['qty'] for x in ls)
  if abs(lq-filled)>1e-8:raise Blocked('对冲腿部分成交；禁止盲目补单，需核实')
  lt=sum(x['qty']*x['price'] for x in ls)/lq
  if closing:
   p=self.position
   if filled<p['qty']-1e-9:
    ratio=(p['qty']-filled)/p['qty'];self.position={**p,'qty':p['qty']-filled,'fees':p['fees']*ratio}
   else:self.position=None
   self.state['lastClose']=time.time()
  else:self.position.update(lt=lt,fees=self.position['fees']+sum(x['fee'] for x in ls),unhedged=False)
  await self.verify();self.persist();return True
 async def emergency_reduce(self):
  # Only this session's confirmed residuals; never touch ambiguous/shared inventory.
  if other_service_active() or any(i['outcome']=='unknown' for i in self.state['journal']):return False
  await self.verify();b=await self.venue.book()
  for leg in ['io','lt']:
   q=self.ledger.qty(leg)
   if abs(q)>1e-9:
    side=-1 if q>0 else 1;p=vwap(b[leg]['bids' if side==-1 else 'asks'],abs(q))
    await self.execute(leg,side,abs(q),p,'emergency-close')
    await self.verify()
  if any(abs(self.ledger.qty(k))>1e-9 for k in ['io','lt']):return False
  await self.venue.no_orders();self.position=None;self.persist();return True
 async def run(self):
  started=time.time();samples=0;draining=False
  try:
   while True:
    b=await self.refresh();samples+=1
    reason=stop_reason(self.config,self.ledger.total,self.state['riskEquity'])
    draining=draining or bool(reason) or self.stop.is_set()
    if draining:self.state.update(phase='draining',message='停止新开仓并收尾：'+str(reason or 'user'))
    p=self.position
    if p:
     age=time.time()-p['opened'];sgn=1 if p['dir']=='S' else -1
     io=vwap(b['io']['asks' if sgn==1 else 'bids'],p['qty']);lt=vwap(b['lt']['bids' if sgn==1 else 'asks'],p['qty']);mark=p['qty']*sgn*(p['io']-io+lt-p['lt'])-p['fees']-p['qty']*(io*.0003+lt*.00028)
     emergency=draining or age>=600 or mark/(p['qty']*p['io'])<=-.003
     self.state['phase']='draining' if draining else 'position';await self.pair(b,None,True,emergency)
    elif draining:
     await self.venue.no_orders();await self.verify();self.state.update(phase='finished',message='已停止；本程序订单终态已确认且两所仓位归零');break
    elif time.time()-started>=60 and samples>=10 and time.time()-self.state.get('lastClose',0)>=10:
     a=entry(b,self.config,self.venue.io_dec,self.venue.lt_dec)
     self.state['phase']='quoting' if a else 'cost-risk'
     if a:await self.pair(b,a)
    else:self.state['phase']='warmup' if time.time()-started<60 else 'cooldown'
    self.persist();await asyncio.sleep(1)
  except BaseException as e:
   # Never erase ambiguous orders or inventory, never auto-resume after process restart.
   cleaned=False
   try:cleaned=await self.emergency_reduce()
   except Exception:pass
   self.state.update(phase='finished-error' if cleaned else 'needs_review',message=('异常后确认仓位已归零；本会话结束。' if cleaned else '异常冻结；仍需核对挂单/敞口。')+(str(e) if isinstance(e,Blocked) else '接口异常'))
  finally:
   self.running=False;self.persist()
   if self.venue:await self.venue.close()
