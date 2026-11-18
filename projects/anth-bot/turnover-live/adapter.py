"""SDK bridge. No private config, signing, leverage writes, or orders at import time.
User-started process alone reads its existing server environment. Never expose SDK
exceptions or account identifiers over HTTP. Tested signatures: HL .24 / LT 1.1.8.
"""
import asyncio,math,os,time
from decimal import Decimal,ROUND_CEILING,ROUND_FLOOR
import aiohttp
from core import Blocked,number
COIN='io:ANTH';MID=193;HL='https://api.hyperliquid.xyz/info';LT='https://mainnet.zklighter.elliot.ai'

def hl_price(price,sz_dec,buy,maker):
 d=Decimal(str(price))
 if not d.is_finite() or d<=0:raise Blocked('Hyperliquid价格无效')
 # <=5 significant figures AND <=6-szDecimals decimals; integer always valid.
 decimals=max(0,min(6-sz_dec,4-d.adjusted()))
 mode=ROUND_FLOOR if (buy and maker) or (not buy and not maker) else ROUND_CEILING
 p=d.quantize(Decimal(1).scaleb(-decimals),rounding=mode)
 if p<=0:raise Blocked('Hyperliquid价格小于最小价格单位')
 return float(p)

class Venue:
 def __init__(self):self.http=None;self.hl=None;self.lt=None;self.key_index=None;self.io_dec=3;self.lt_dec=5;self.price_dec=1;self.min_free=26
 async def initialize(self):
  # This method is never invoked by page view, status, deployment or server startup.
  from eth_account import Account
  from hyperliquid.exchange import Exchange
  from hyperliquid.utils.constants import MAINNET_API_URL
  import lighter
  names=['HL_ACCOUNT_ADDRESS','HL_API_PRIVATE_KEY','LIGHTER_ACCOUNT_INDEX','LIGHTER_API_KEY_INDEX','LIGHTER_API_PRIVATE_KEY']
  if any(not os.environ.get(k) for k in names):raise Blocked('服务端缺少现有账户授权；网页不接收密钥')
  self.address=os.environ['HL_ACCOUNT_ADDRESS'];self.index=int(os.environ['LIGHTER_ACCOUNT_INDEX'])
  self.hl=await asyncio.to_thread(Exchange,Account.from_key(os.environ['HL_API_PRIVATE_KEY']),MAINNET_API_URL,account_address=self.address,perp_dexs=['','io'],timeout=5)
  self.key_index=int(os.environ['LIGHTER_API_KEY_INDEX'])
  if not 0<=self.key_index<255:raise Blocked('Lighter API索引不在有效范围')
  self.lt=lighter.SignerClient(url=LT,account_index=self.index,api_private_keys={self.key_index:os.environ['LIGHTER_API_PRIVATE_KEY']})
  self.accounts=lighter.AccountApi(self.lt.api_client)
  self.http=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))
  # Deliberately no check_client/update_leverage/create_order here.
 def auth(self):
  if self.key_index is None:raise Blocked('Lighter认证索引未初始化')
  token,err=self.lt.create_auth_token_with_expiry(deadline=600,api_key_index=self.key_index)
  if err or not token:raise Blocked('Lighter只读认证失败')
  return token
 async def close(self):
  if self.http:await self.http.close()
  if self.lt:await self.lt.close()
 async def post(self,payload):
  async with self.http.post(HL,json=payload) as r:r.raise_for_status();return await r.json()
 async def get(self,path,params=None,auth=False):
  async with self.http.get(LT+'/api/v1/'+path,params=params,headers={'Authorization':self.auth()} if auth else {}) as r:r.raise_for_status();d=await r.json()
  if d.get('code')!=200:raise Blocked('Lighter查询未返回成功状态')
  return d
 async def book(self):
  start=time.time();io,lt=await asyncio.gather(self.post({'type':'l2Book','coin':COIN}),self.get('orderBookOrders',{'market_id':MID,'limit':20}))
  if io.get('coin')!=COIN or abs(time.time()*1000-float(io['time']))>3000 or time.time()-start>3:raise Blocked('行情延迟或市场标识不符，不发送订单')
  b={'io':{'bids':[[float(x['px']),float(x['sz'])] for x in io['levels'][0]],'asks':[[float(x['px']),float(x['sz'])] for x in io['levels'][1]]},'lt':{side:[[float(x['price']),float(x['remaining_base_amount'])] for x in lt[side]] for side in ['bids','asks']},'at':time.time()}
  for venue in ['io','lt']:
   x=b[venue]
   if not x['bids'] or not x['asks'] or x['bids'][0][0]>=x['asks'][0][0]:raise Blocked('盘口为空或交叉')
   for rows in x.values():
    if any(not number(v) or v<=0 for row in rows for v in row):raise Blocked('盘口数值无效')
  return b
 async def exposure(self):
  io,lt=await asyncio.gather(self.post({'type':'clearinghouseState','user':self.address,'dex':'io'}),self.get('account',{'by':'index','value':str(self.index)}))
  if not isinstance(io.get('assetPositions'),list) or len(lt.get('accounts',[]))!=1:raise Blocked('仓位响应不完整')
  account=lt['accounts'][0]
  if account.get('index',account.get('account_index'))!=self.index or not isinstance(account.get('positions'),list):raise Blocked('账户/仓位不符')
  pio=sum(float(x['position']['szi']) for x in io['assetPositions'] if x['position']['coin']==COIN)
  plt=0
  for p in account['positions']:
   if p['market_id']==MID and float(p['position'])!=0:
    if int(p['sign']) not in {-1,1}:raise Blocked('持仓方向无效')
    plt+=float(p['position'])*int(p['sign'])
  if not all(number(x) for x in [pio,plt]):raise Blocked('未知仓位')
  return {'io':pio,'lt':plt}
 async def no_orders(self):
  io,lt=await asyncio.gather(self.post({'type':'openOrders','user':self.address,'dex':'io'}),self.lt.order_api.account_active_orders(authorization=self.auth(),account_index=self.index,market_id=MID,_request_timeout=5.0))
  d=lt.to_dict()
  if not isinstance(io,list) or d.get('code')!=200 or not isinstance(d.get('orders'),list):raise Blocked('挂单状态未知')
  if any(x.get('coin')==COIN for x in io) or d['orders']:raise Blocked('同市场已有挂单；禁止共用账户启动')
 async def preflight(self):
  p=await self.exposure()
  if any(abs(x)>1e-10 for x in p.values()):raise Blocked('同市场已有仓位，禁止归属混淆')
  await self.no_orders()
  io,lt=await asyncio.gather(self.post({'type':'meta','dex':'io'}),self.get('orderBookDetails',{'market_id':MID}))
  m=next((x for x in io.get('universe',[]) if x['name'] in {COIN,'ANTH'}),None)
  rows=[x for x in lt.get('order_book_details',[]) if x.get('market_id')==MID]
  if not m or len(rows)!=1 or rows[0].get('symbol')!='ANTHROPIC':raise Blocked('市场元数据不匹配')
  self.io_dec=int(m['szDecimals']);self.lt_dec=int(rows[0]['size_decimals']);self.price_dec=int(rows[0]['price_decimals'])
  # Existing account leverage is read-only; nominal exposure is still capped at 50U.
  # Never impose a new 1x setting on the user's existing 3x arbitrage account.
  io_state,account,spot,ab=await asyncio.gather(self.post({'type':'clearinghouseState','user':self.address,'dex':'io'}),self.get('account',{'by':'index','value':str(self.index)}),self.post({'type':'spotClearinghouseState','user':self.address}),self.post({'type':'userAbstraction','user':self.address}))
  io_free=float(io_state['withdrawable']);lt_free=float(account['accounts'][0]['available_balance'])
  if isinstance(ab,str) and ab in {'unifiedAccount','portfolioMargin'}:
   usdc=next((x for x in spot['balances'] if x['coin']=='USDC'),None)
   if usdc is None:raise Blocked('统一账户USDC余额未知')
   io_free=max(io_free,float(usdc['total'])-float(usdc['hold']))
  if not all(number(x) and x>=self.min_free for x in [io_free,lt_free]):raise Blocked('两所可用余额不足本次每腿名义额加费用余量')
  if await self.funding(time.time()) is None:raise Blocked('资金费账单接口未核实')
 async def funding(self,since):
  # SDK 1.1.8 uses plural market_ids/start_timestamp (milliseconds).
  # Do not swallow exceptions: controller retries transient READS and records sanitized origin.
  io,lt=await asyncio.gather(self.post({'type':'userFunding','user':self.address,'startTime':int(since*1000)}),self.accounts.position_funding(account_index=self.index,limit=100,authorization=self.auth(),market_ids=[MID],start_timestamp=int(since*1000),_request_timeout=5.0))
  d=lt.to_dict()
  if not isinstance(io,list) or len(io)>=500 or d.get('code')!=200 or not isinstance(d.get('position_fundings'),list) or d.get('next_cursor'):return None
  n=sum(float(x['delta']['usdc']) for x in io if x['delta']['coin']==COIN)
  n+=sum(float(x['change']) for x in d['position_fundings'] if x['market_id']==MID and x['timestamp']*1000>=since*1000)
  return n if number(n) else None
 async def submit(self,i,maker=False):
  buy=i['side']==1
  if i['venue']=='io':
   from hyperliquid.utils.types import Cloid
   price=i['price'] if maker else i['price']*(1.003 if buy else .997)
   price=hl_price(price,self.io_dec,buy,maker)
   r=await asyncio.wait_for(asyncio.to_thread(self.hl.order,COIN,buy,i['qty'],price,{'limit':{'tif':'Alo' if maker else 'Ioc'}},i['reduce'],cloid=Cloid.from_str('0x'+i['client'])),7)
   if r.get('status')!='ok':raise Blocked('IO提交结果无法确认')
   rows=r['response']['data']['statuses']
   if len(rows)!=1:raise Blocked('IO订单回报数量异常')
   x=rows[0]
   if 'error' in x:return {'rejected':True,'id':None,'terminal':True}
   if 'filled' in x:return {'id':int(x['filled']['oid']),'filled':float(x['filled']['totalSz']),'terminal':True,'maker':maker}
   if 'resting' in x:return {'id':int(x['resting']['oid']),'terminal':False,'maker':maker}
   raise Blocked('IO未获得订单标识')
  scale=10**self.price_dec;p=i['price']*(1.003 if buy else .997)
  pint=int((Decimal(str(p))*scale).to_integral_value(rounding=ROUND_CEILING if buy else ROUND_FLOOR))
  amount=Decimal(str(i['qty']))*(10**self.lt_dec)
  if amount<=0 or amount!=amount.to_integral_value():raise Blocked('Lighter数量不能精确表示；禁止静默舍入对冲量')
  _,resp,err=await asyncio.wait_for(self.lt.create_market_order(market_index=MID,client_order_index=i['client'],base_amount=int(amount),avg_execution_price=pint,is_ask=not buy,reduce_only=i['reduce']),7)
  if err or resp is None or getattr(resp,'code',None)!=200:raise Blocked('Lighter提交结果未知；禁止自动重发')
  return {'id':None,'terminal':False,'maker':False}
 async def order(self,i):
  if i['venue']=='io':
   d=await self.post({'type':'orderStatus','user':self.address,'oid':'0x'+i['client']})
   if d.get('status')!='order':return None
   x=d['order']['order'];status=d['order']['status']
   if x.get('coin')!=COIN or x.get('cloid')!='0x'+i['client'] or x.get('side')!=('B' if i['side']==1 else 'A') or abs(float(x['origSz'])-i['qty'])>1e-9:raise Blocked('IO订单身份不匹配')
   # Filled may retain orig size in orderStatus; terminal fill amount is trade evidence,
   # never infer remaining from this endpoint without matching user fills.
   return {'state':status,'id':int(x['oid']),'filled':None if status=='filled' else i['qty']-float(x['sz']),'terminal':status=='filled' or status.endswith('Canceled') or status in {'canceled','rejected'}}
  d=(await self.lt.order_api.account_orders(authorization=self.auth(),client_order_indexes=str(i['client']),account_index=self.index,_request_timeout=5.0)).to_dict()
  if d.get('code')!=200:return None
  rows=[x for x in d.get('orders',[]) if x['client_order_index']==i['client']]
  if len(rows)!=1:return None
  x=rows[0]
  if x['owner_account_index']!=self.index or x['market_index']!=MID or x['is_ask']!=(i['side']==-1) or x['reduce_only']!=i['reduce'] or abs(float(x['initial_base_amount'])-i['qty'])>1e-9:raise Blocked('Lighter订单身份不匹配')
  return {'state':x['status'],'id':int(x['order_index']),'filled':float(x['filled_base_amount']),'terminal':x['status']=='filled' or x['status'].startswith('canceled')}
 async def fills(self,i,oid):
  if i['venue']=='io':
   rows=await self.post({'type':'userFillsByTime','user':self.address,'startTime':int((i['created']-1)*1000),'aggregateByTime':False})
   if not isinstance(rows,list) or len(rows)>=2000:raise Blocked('IO成交页不完整')
   out=[]
   for x in rows:
    if x.get('coin')==COIN and x.get('oid')==oid:
     if x.get('side')!=('B' if i['side']==1 else 'A') or x.get('feeToken')!='USDC':raise Blocked('IO成交方向/计费币种未确认')
     out.append(dict(venue='io',id=str(x['tid']),side=i['side'],qty=float(x['sz']),price=float(x['px']),fee=float(x['fee']),pnl=float(x['closedPnl'])))
   return out
  d=(await self.lt.order_api.trades(sort_by='timestamp',limit=100,authorization=self.auth(),market_id=MID,account_index=self.index,order_index=oid,aggregate=False,_request_timeout=5.0)).to_dict()
  if d.get('code')!=200 or d.get('next_cursor'):raise Blocked('Lighter成交账单不完整')
  out=[];trades=[];missing=False
  for x in d['trades']:
   ask=i['side']==-1;prefix='ask' if ask else 'bid'
   if x[prefix+'_id']!=oid:continue
   if x['market_id']!=MID or x[prefix+'_account_id']!=self.index or int(x[prefix+'_client_id'])!=i['client']:raise Blocked('Lighter成交身份不匹配')
   trades.append(x);maker=x['is_maker_ask']==ask;role='maker' if maker else 'taker'
   if x.get(role+'_fee') is None or x.get('integrator_'+role+'_fee') is None or x.get('integrator_'+role+'_fee') or x.get(prefix+'_account_pnl') is None:
    missing=True;continue
   q=float(x['size']);p=float(x['price']);fee=q*p*float(x[role+'_fee'])/1e6
   out.append(dict(venue='lt',id=str(x['trade_id']),side=i['side'],qty=q,price=p,fee=fee,pnl=float(x[prefix+'_account_pnl'])))
  if missing:return await self.aggregate_fills(i,oid,trades)
  return out
 async def aggregate_fills(self,i,oid,trades):
  # Missing raw fields never mean zero. Match official aggregates to exact raw fills.
  d=await self.get('aggregatedTrades',{'account_index':self.index,'market_id':MID,'order_index_str':str(oid),'limit':100})
  if d.get('code')!=200 or d.get('next_cursor') or not d.get('trades'):raise Blocked('聚合成交账单尚未完整')
  out=[];roles=set()
  for x in d['trades']:
   if x.get('account_index')!=self.index or str(x.get('order_id'))!=str(oid) or x.get('market_id')!=MID or x.get('is_ask')!=(i['side']<0) or x.get('type')!='trade' or x.get('role') not in {'maker','taker'}:raise Blocked('聚合账单订单归属不匹配')
   role=x['role']
   if role in roles:raise Blocked('聚合成交分组重复')
   roles.add(role);raw=[t for t in trades if ('maker' if t['is_maker_ask']==(i['side']<0) else 'taker')==role]
   q=sum(float(t['size']) for t in raw);value=sum(float(t['size'])*float(t['price']) for t in raw)
   if not raw or x.get('fill_count')!=len(raw) or abs(float(x['size'])-q)>1e-9 or abs(float(x['usd_amount'])-value)>1e-5 or str(x.get('first_trade_id'))!=str(min(t['trade_id'] for t in raw)) or str(x.get('last_trade_id'))!=str(max(t['trade_id'] for t in raw)):raise Blocked('聚合账单与逐笔成交不一致')
   if x.get('fee_asset_id')!=3 or x.get('fee') is None or x.get('integrator_fee') is None:raise Blocked('聚合费用币种或金额未知')
   pnl=x.get('pnl')
   if pnl is None:
    if i.get('reduce'):raise Blocked('平仓已实现盈亏未知')
    pnl=0 # Official schema: null when these fills did not reduce a position.
   fee=float(x['fee'])+float(x['integrator_fee']);pnl=float(pnl)
   if not all(number(z) for z in [q,value,fee,pnl]) or q<=0 or value<=0:raise Blocked('聚合账单数值无效')
   out.append(dict(venue='lt',id=f'aggregate:{oid}:{role}',side=i['side'],qty=q,price=value/q,fee=fee,pnl=pnl))
  if abs(sum(z['qty'] for z in out)-sum(float(t['size']) for t in trades))>1e-9:raise Blocked('聚合账单存在缺失成交')
  return out
 async def settle(self,i,ack,stop):
  if ack.get('rejected'):return {'terminal':True,'fills':[]}
  end=time.monotonic()+18;cancelled=False;latest=None
  while time.monotonic()<end:
   o=await self.order(i)
   if o:
    latest=o
    if o['terminal']:
     f=await self.fills(i,o['id']);q=sum(x['qty'] for x in f)
     expected=i['qty'] if o['filled'] is None else o['filled']
     if abs(q-expected)<1e-8:return {'terminal':True,'fills':f}
    # Partial maker fill: cancel remainder once, then hedge only after cancel terminal.
    if ack['maker'] and not cancelled and not o['terminal'] and (stop.is_set() or (o['filled'] or 0)>0 or time.time()-i['created']>=15):
     from hyperliquid.utils.types import Cloid
     cancelled=True
     await asyncio.wait_for(asyncio.to_thread(self.hl.cancel_by_cloid,COIN,Cloid.from_str('0x'+i['client'])),5)
   await asyncio.sleep(.4)
  raise Blocked('订单终态/成交账单未及时确认；冻结，禁止重发')
