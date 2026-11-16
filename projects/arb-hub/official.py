"""Documented public endpoints, strict identities, audited independent caches."""
import json, math, threading, urllib.request
from runtime import ScheduledCache, utcnow
from datetime import datetime, timezone, timedelta
from pathlib import Path
from itertools import combinations

URLS={
'Binance':['https://www.binance.com/fapi/v1/premiumIndex','https://www.binance.com/fapi/v1/fundingInfo','https://www.binance.com/fapi/v1/exchangeInfo','https://www.binance.com/api/v3/ticker/price'],
'Gate':['https://api.gateio.ws/api/v4/futures/usdt/contracts','https://api.gateio.ws/api/v4/spot/tickers'],
'Bitget':['https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES','https://api.bitget.com/api/v2/mix/market/current-fund-rate?productType=USDT-FUTURES','https://api.bitget.com/api/v2/spot/market/tickers'],
'OKX':['https://www.okx.com/api/v5/market/tickers?instType=SWAP','https://www.okx.com/api/v5/market/tickers?instType=SPOT','https://www.okx.com/api/v5/public/instruments?instType=SWAP'],
'Hyperliquid':['https://api.hyperliquid.xyz/info'],
'Entropy':['https://api.hyperliquid.xyz/info'],
'Lighter':['https://mainnet.zklighter.elliot.ai/api/v1/orderBookDetails','https://mainnet.zklighter.elliot.ai/api/v1/funding-rates']}
MEANINGS={'Binance':'lastFundingRate：上次已结算/接口 last，不是预测','Gate':'funding_rate：当前/接口费率；funding_rate_indicative：指示/预测，独立展示','Bitget':'current-fund-rate fundingRate：当前资金费','Hyperliquid':'funding：接口当前值，周期字段缺失','Lighter':'仅 exchange=lighter；rate 原值，周期未知','OKX':'仅行情；资金费需逐标的，本版未做全量资金费','Entropy':'HIP-3 io 市场列表；独立身份，禁止与 CEX 配对'}
def stamp(): return datetime.now(timezone.utc).isoformat()
def num(x):
    try:
        n=float(x)
        return n if math.isfinite(n) else None
    except (ValueError,TypeError): return None
def market(exchange,base,settle,rate=None,hours=None,kind='linear',multiplier=1,rate_kind=None,**kw):
    h=num(hours)
    return dict(exchange=exchange,base=base,settle=settle,kind=kind,multiplier=num(multiplier),rate=num(rate),rate_kind=rate_kind,hours=h if h and h>0 else None,meaning=MEANINGS.get(exchange,''),**kw)
def spot(exchange,base,quote,price): return market(exchange,base,quote,kind='spot',price=num(price),price_field='现货最新价',official_identity=None)
def parse_binance(raw):
    rates,info,contracts,prices=raw
    cycles={r['symbol']:r.get('fundingIntervalHours') for r in info}
    meta={r['symbol']:r for r in contracts['symbols'] if r.get('contractType')=='PERPETUAL' and r.get('status')=='TRADING' and r.get('quoteAsset')==r.get('marginAsset')=='USDT'}
    out=[market('Binance',meta[r['symbol']]['baseAsset'],'USDT',r.get('lastFundingRate'),cycles.get(r['symbol']),rate_kind='last_settled',price=num(r.get('markPrice')),price_field='markPrice',official_identity=(meta[r['symbol']]['baseAsset'],'USDT',meta[r['symbol']].get('underlyingType')),contract=r['symbol'],source_time=r.get('time'),next_time=r.get('nextFundingTime')) for r in rates if r['symbol'] in meta]
    # Exact spot symbol from contract metadata; never strip an assumed prefix/multiplier.
    out.extend(spot('Binance',meta[r['symbol']]['baseAsset'],'USDT',r.get('price')) for r in prices if r['symbol'] in meta)
    for m in out:
        if m['kind']=='spot': m['official_identity']=(m['base'],'USDT',meta[m['base']+'USDT'].get('underlyingType'))
    return out

def parse_gate(raw):
    contracts,prices=raw
    out=[]
    for r in contracts:
        if r.get('in_delisting') or not r['name'].endswith('_USDT'): continue
        out.append(market('Gate',r['name'][:-5],'USDT',r.get('funding_rate'),(num(r.get('funding_interval')) or 0)/3600,rate_kind='current',price=num(r.get('mark_price')),price_field='mark_price',indicative=num(r.get('funding_rate_indicative')),official_identity=r['name'],contract=r['name'],next_time=r.get('funding_next_apply')))
    out.extend(spot('Gate',r['currency_pair'][:-5],'USDT',r.get('last')) for r in prices if r['currency_pair'].endswith('_USDT'))
    for m in out:
        if m['kind']=='spot': m['official_identity']=m['base']+'_USDT'
    return out

def parse_bitget(raw):
    tick,rate,prices=[r['data'] for r in raw]
    rates={r['symbol']:r for r in rate}
    out=[]
    for r in tick:
        if not r['symbol'].endswith('USDT') or r['symbol'] not in rates: continue
        f=rates[r['symbol']]
        out.append(market('Bitget',r['symbol'][:-4],'USDT',f.get('fundingRate'),f.get('fundingRateInterval'),rate_kind='current',price=num(r.get('markPrice')),price_field='markPrice',official_identity=r['symbol'],contract=r['symbol'],source_time=r.get('ts'),next_time=f.get('nextUpdate')))
    out.extend(spot('Bitget',r['symbol'][:-4],'USDT',r.get('lastPr')) for r in prices if r['symbol'].endswith('USDT'))
    for m in out:
        if m['kind']=='spot': m['official_identity']=m['base']+'USDT'
    return out

def parse_okx(raw):
    tick,prices,meta=[r['data'] for r in raw]
    allowed={r['instId']:r for r in meta if r.get('ctType')=='linear' and r.get('settleCcy')=='USDT' and r.get('state')=='live' and num(r.get('ctMult',1))==1}
    out=[]
    for r in tick:
        if r['instId'] not in allowed: continue
        m=allowed[r['instId']]; base=m.get('uly','').removesuffix('-USDT')
        if not base: continue
        out.append(market('OKX',base,'USDT',price=num(r.get('last')),price_field='last（最新价）',official_identity=m.get('uly') or m.get('instFamily'),contract=r['instId'],ctVal=m.get('ctVal'),source_time=r.get('ts')))
    out.extend(spot('OKX',r['instId'][:-5],'USDT',r.get('last')) for r in prices if r['instId'].endswith('-USDT'))
    for m in out:
        if m['kind']=='spot': m['official_identity']=m['base']+'-USDT'
    return out

def parse_hl(raw,exchange='Hyperliquid'):
    meta,ctx=raw[0]
    return [market(exchange,m['name'],'USDC',c.get('funding'),rate_kind='current',kind='hip3' if exchange=='Entropy' else 'linear',price=num(c.get('markPx')),price_field='markPx',contract=m['name']) for m,c in zip(meta['universe'],ctx) if not m.get('isDelisted')]

def parse_lighter(raw):
    books,funds=raw
    rates={str(r['market_id']):r for r in funds.get('funding_rates',[]) if r.get('exchange')=='lighter'}
    out=[]
    for r in books.get('order_book_details',[]):
        if r.get('market_type')!='perp' or r.get('status')!='active': continue
        f=rates.get(str(r.get('market_id')), {})
        # No documented settlement field here: never infer USD/USDC identity.
        out.append(market('Lighter',r['symbol'],'未知',f.get('rate'),rate_kind='current',multiplier=r.get('multiplier'),price=num(r.get('mark_price')),price_field='mark_price',contract=str(r.get('market_id'))))
    return out
PARSERS={'Binance':parse_binance,'Gate':parse_gate,'Bitget':parse_bitget,'OKX':parse_okx,'Hyperliquid':parse_hl,'Entropy':lambda r:parse_hl(r,'Entropy'),'Lighter':parse_lighter}
def fetch_json(url,body=None):
    data=json.dumps(body).encode() if body else None
    req=urllib.request.Request(url,data=data,headers={'User-Agent':'arb-hub-readonly/2','Accept':'application/json',**({'Content-Type':'application/json'} if data else {})})
    with urllib.request.urlopen(req,timeout=12) as response:
        content=response.read(16*1024*1024+1)
        if len(content)>16*1024*1024: raise ValueError('响应超过 16 MiB')
        return json.loads(content)
def collect(name):
    raw=[]
    for url in URLS[name]:
        body={'type':'metaAndAssetCtxs'} if name in ('Hyperliquid','Entropy') else None
        if name=='Entropy': body['dex']='io'
        raw.append(fetch_json(url,body))
    return raw,PARSERS[name](raw)
def identity(m): return (m['base'],m['settle'],m['kind'],m['multiplier'])
def pair_identity(a,b):
    """Price is a veto, never proof of cross-exchange asset identity."""
    prices=[num(m.get('price')) for m in (a,b)]
    if any(p is None or p<=0 for p in prices): return 'identity_unverified',None
    ratio=max(prices)/min(prices)
    if ratio>=2: return 'anomaly',ratio
    if a['exchange']==b['exchange'] and a.get('official_identity') and a['official_identity']==b.get('official_identity'):
        return 'verified',ratio
    return ('identity_unverified' if a['exchange']==b['exchange'] else 'candidate'),ratio

def comparable(row): return row['status'] in ('verified','candidate')

def eligible(m): return m['kind']=='linear' and m['multiplier']==1 and m['settle']!='未知'
def payment(rate): return '多头支付/空头收取' if rate>0 else ('空头支付/多头收取' if rate<0 else '零费率，无支付')
def next_iso(value):
    value=num(value)
    if value is None or value < 1e9: return None
    try: return datetime.fromtimestamp(value/1000 if value>1e12 else value,timezone.utc).isoformat()
    except (ValueError,OverflowError,OSError): return None

def funding_routes(markets, generated_at=None):
    generated_at=generated_at or datetime.now(timezone.utc)
    groups={}
    for original in markets:
        m=dict(original)
        m['next_iso']=next_iso(m.get('next_time'))
        m['next_expired']=bool(m['next_iso'] and datetime.fromisoformat(m['next_iso'])<generated_at)
        if eligible(m) and m['rate'] is not None: groups.setdefault(identity(m),{})[m['exchange']]=m
    out=[]
    for key,venues in groups.items():
        for a,b in combinations(venues.values(),2):
            known=a['hours'] is not None and b['hours'] is not None
            # Unknown periods cannot establish comparable direction; raw ordering is explicitly provisional.
            lo,hi=sorted((a,b),key=lambda m:m['rate']/m['hours'] if known else m['rate'])
            identity_status,ratio=pair_identity(a,b)
            status='unknown' if not known else ('mixed' if a.get('rate_kind')!=b.get('rate_kind') else identity_status)
            fields='；'.join(f"{m['exchange']} {m.get('rate_kind') or 'unknown'}（{m['meaning']}）" for m in (lo,hi))
            conflict=('两腿均为当前资金费，采集时刻可能相差数秒，仍非盘口可成交净利' if status in ('candidate','verified') else fields+'；不是同一时点同一口径，不可当作高精度套利净利')
            if not known: conflict+='；周期未知，不能扣费后当作净利'
            expired=lo['next_expired'] or hi['next_expired']
            if expired: conflict+='；下次资金费时间已过期'
            out.append(dict(coin=key[0],strategy='funding',long_platform=lo['exchange'],short_platform=hi['exchange'],long=lo,short=hi,hourly_diff=hi['rate']/hi['hours']-lo['rate']/lo['hours'] if known else None,period=f"{lo['hours']}h / {hi['hours']}h",direction=f"多 {lo['exchange']}（{payment(lo['rate'])}），空 {hi['exchange']}（{payment(hi['rate'])}）"+('；周期未知，仅按原值暂列方向，不可比较' if not known else ''),conflict=conflict,next_expired=expired,comparison_status=status,identity_status=identity_status,anomaly=identity_status=='anomaly',price_ratio=ratio,history='未做全量历史拉取',status=status))
    return sorted(out,key=lambda r:(r['hourly_diff'] is None,-(r['hourly_diff'] or 0)))
def basis_routes(markets):
    spots={}
    for m in markets:
        if m['kind']=='spot' and m.get('price') and m['price']>0: spots.setdefault((m['base'],m['settle']),{})[m['exchange']]=m
    out=[]; seen=set()
    for m in markets:
        if not eligible(m) or not m.get('price') or m['price']<=0: continue
        for s in spots.get((m['base'],m['settle']),{}).values():
            key=(identity(m),s['exchange'],m['exchange'])
            if key in seen: continue
            seen.add(key)
            status,ratio=pair_identity(s,m)
            out.append(dict(coin=m['base'],strategy='spread',long_platform=s['exchange'],short_platform=m['exchange'],long=s,short=m,basis=m['price']/s['price']-1,period='报价快照',status=status,identity_status=status,anomaly=status=='anomaly',price_ratio=ratio,direction='多现货 / 空永续（正 basis 筛选方向；负值不代表可卖空现货）'))
    return sorted(out,key=lambda r:-r['basis'])
def fees(rate,notional): return 4*rate*notional
class SourceCache(ScheduledCache):
    def __init__(self,directory,name,clock=utcnow,interval=30,metadata_interval=900,jitter=None):
        self.name=name; self.path=Path(directory)/(name.lower()+'.json'); self.lock=threading.RLock()
        self.metadata_interval=metadata_interval; self.metadata_at=None
        try:self.state=json.loads(self.path.read_text())
        except (OSError,ValueError):self.state={}
        self.setup(clock,interval,jitter)
    def view(self):
        with self.lock:
            s={k:self.state.get(k) for k in ('error','last_success','last_attempt','provenance')}
            s.update(self.status(self.state)); count=len(self.state.get('markets',[]))
        note=('该源行情与规格同接口，按行情周期请求' if self.name in ('Gate','Bitget','Hyperliquid','Entropy') else
              f'规格/周期表每 {self.metadata_interval} 秒请求' if self.name in ('Binance','OKX') else '公开批量行情与费率')
        s.update(name=self.name,rows=count,method='官方公开 API',field_notes=MEANINGS[self.name],schedule_notes=note,metadata_interval_s=self.metadata_interval if self.name in ('Binance','OKX') else self.interval)
        return s
    def collect_cached(self):
        indexes={'Binance':{1,2},'OKX':{2}}.get(self.name,set())
        with self.lock: previous=self.state.get('raw')
        meta_due=self.metadata_at is None or (self.clock()-self.metadata_at).total_seconds()>=self.metadata_interval
        raw=[]
        for i,url in enumerate(URLS[self.name]):
            if i in indexes and not meta_due and isinstance(previous,list) and len(previous)>i:
                raw.append(previous[i]);continue
            body={'type':'metaAndAssetCtxs'} if self.name in ('Hyperliquid','Entropy') else None
            if self.name=='Entropy':body['dex']='io'
            raw.append(fetch_json(url,body))
        rows=PARSERS[self.name](raw)
        if indexes and meta_due:self.metadata_at=self.clock()
        return raw,rows
    def refresh(self,fetch=None,parse=None):
        def collect_data():
            if fetch:
                raw=fetch();return raw,parse(raw)
            return self.collect_cached()
        def apply(result):
            raw,rows=result
            if not rows:raise ValueError('未解析到有效市场行')
            times=[next_iso(m.get('source_time')) for m in rows]; times=[t for t in times if t]
            t=self.clock().isoformat()
            return dict(raw=raw,markets=rows,quote_time=max(times) if times else None,
                provenance={'source':self.name,'urls':URLS[self.name],'http_status':200,'fetched_at':t,'parsed_rows':len(rows),'fields':MEANINGS[self.name]})
        return self.run_refresh(collect_data,apply)
class Hub:
    def __init__(self,directory,**options):self.caches={n:SourceCache(directory,n,**options) for n in URLS}
    def refresh(self,force=False):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=7) as pool:
            list(pool.map(lambda c:c.refresh() if force or c.due() else None,self.caches.values()))
    def view(self):
        sources=[c.view() for c in self.caches.values()]; markets=[]
        for c,s in zip(self.caches.values(),sources):
            with c.lock:
                # Reparse saved raw with current semantics without fetching or rewriting caches.
                saved=c.state.get('markets',[])
                if isinstance(c.state.get('raw'),list): saved=PARSERS[c.name](c.state['raw'])
                markets.extend(dict(m,last_success=s.get('last_success'),stale=s['stale'],stale_after_s=s['stale_after_s']) for m in saved)
        rows=funding_routes(markets)+basis_routes(markets)
        return {'official_sources':sources,'official_rows':rows}
