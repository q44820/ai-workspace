"""Standalone read-only HTTP dashboard, standard library only."""
import argparse
import concurrent.futures as cf
import itertools
import json
import math
import os
from pathlib import Path
import sqlite3
import statistics
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from core import validate_symbol, pair_stats, spread_bp, vwap
from exchanges import load, LABELS

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('BASIS_DB', str(ROOT / 'data' / 'quotes.sqlite3')))
DB.parent.mkdir(parents=True, exist_ok=True)
ACTIVE = {}
CACHE = {}
LOCK = threading.Lock()
SEMAPHORE = threading.BoundedSemaphore(3)

def connect():
    db = sqlite3.connect(str(DB), timeout=10)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS samples (symbol TEXT, market TEXT, venue TEXT, minute INTEGER, mid REAL, PRIMARY KEY(symbol,market,venue,minute))')
    return db


def collect(symbol, market, venues):
    end = int(time.time()) // 60 * 60
    quotes, errors = [], []
    legs = [(v,m) for v in venues for m in (('spot','perp') if market == 'spot_perp' else (market,))
            if not (m == 'spot' and v in ('lighter','arcus'))]
    def one(leg):
        v,m = leg
        try: return v, m, load(v, symbol, m, end), None
        except Exception as e: return v, m, None, str(e)[:180]
    with cf.ThreadPoolExecutor(max_workers=max(1,min(6, len(legs)))) as ex:
        for venue, leg_market, q, error in ex.map(one, legs):
            if error: errors.append({'venue': venue, 'market':leg_market, 'name': LABELS[venue] + ('现货' if leg_market == 'spot' else '永续'), 'reason': error})
            else: quotes.append(q)
    now = time.time()
    with connect() as db:
        for q in quotes:
            # One observation per UTC minute, never weighted by number of page refreshes.
            if now-q['received_at'] <= 10 and q['latency_ms'] <= 3000:
                db.execute('INSERT OR IGNORE INTO samples VALUES (?,?,?,?,?)', (symbol,q['market'],q['venue'],int(now)//60*60,(q['bids'][0][0]+q['asks'][0][0])/2))
        db.execute('DELETE FROM samples WHERE minute < ?', (int(now)-86400,))
    return {'quotes': quotes, 'errors': errors, 'end': end, 'fetched_at': now}


def raw_data(symbol, market, venues):
    key = (symbol, market, tuple(sorted(venues)))
    with LOCK:
        cached = CACHE.get(key)
        if cached and time.time() - cached['fetched_at'] < 8: return cached
    with SEMAPHORE:
        d = collect(symbol, market, venues)
    with LOCK:
        if len(CACHE) > 30: CACHE.clear()
        CACHE[key] = d
        # Limited fixed-interval public polling; no LLM, API keys, or trading.
        if (symbol,market) in ACTIVE or len(ACTIVE) < 8:
            ACTIVE[(symbol,market)] = {'venues': venues, 'until': time.time()+14400}
    return d


def live_history(symbol, market, venue, end):
    with connect() as db:
        return dict(db.execute('SELECT minute,mid FROM samples WHERE symbol=? AND market=? AND venue=? AND minute>=? AND minute<?', (symbol,market,venue,end-7200,end)).fetchall())


def analyze(params):
    symbol = validate_symbol(params.get('symbol', ['BTC'])[0])
    market = params.get('market', ['spot'])[0]
    if market not in ('spot','perp','spot_perp'): raise ValueError('market无效')
    venues = list(dict.fromkeys(params.get('venues', ['binance,bybit,bitget,gate'])[0].split(',')))
    minimum = 1 if market == 'spot_perp' else 2
    if not minimum <= len(venues) <= 6 or any(v not in LABELS for v in venues): raise ValueError('请选择足够数量的有效平台；跨市场支持单所现货对永续')
    def number(k, default, lo, hi):
        x = float(params.get(k, [default])[0])
        if not math.isfinite(x) or not lo <= x <= hi: raise ValueError(k+'参数越界')
        return x
    notional = number('notional',1000,10,1000000)
    reserve = number('reserve',3,0,500)
    exit_spread = number('exit_spread',3,0,500)
    threshold = number('threshold',10,0,500)
    fee = {(v,m): number('fee_'+v+'_'+m, number('fee_'+v,5,0,100),0,100)
           for v in venues for m in ('spot','perp')}
    d = raw_data(symbol,market,venues)
    now = time.time(); pairs=[]; skipped=[]
    for a,b in itertools.combinations(d['quotes'],2):
        if market == 'spot_perp':
            if a['market'] == b['market']: continue
            if a['market'] != 'spot': a,b = b,a
        name_a = a['name'] + ('现货' if a['market'] == 'spot' else '永续')
        name_b = b['name'] + ('现货' if b['market'] == 'spot' else '永续')
        if a['quote'] != b['quote']:
            skipped.append(name_a+' / '+name_b+'：USD与USDT计价不同，本版不假设1:1，不混算')
            continue
        bid_a,ask_a = a['bids'][0][0],a['asks'][0][0]
        bid_b,ask_b = b['bids'][0][0],b['asks'][0][0]
        common_qty = notional / ((bid_a+ask_a+bid_b+ask_b)/4)
        a_buy,a_sell = vwap(a['asks'],common_qty),vwap(a['bids'],common_qty)
        b_buy,b_sell = vwap(b['asks'],common_qty),vwap(b['bids'],common_qty)
        depth = all(v is not None for v in [a_buy,a_sell,b_buy,b_sell])
        s = pair_stats(a['candles'],b['candles'],d['end'],a_sell or bid_a,a_buy or ask_a,b_sell or bid_b,b_buy or ask_b,fee[(a['venue'],a['market'])],fee[(b['venue'],b['market'])],reserve,exit_spread)
        live_a=live_history(symbol,a['market'],a['venue'],d['end']);live_b=live_history(symbol,b['market'],b['venue'],d['end'])
        live=pair_stats(live_a,live_b,d['end'],bid_a,ask_a,bid_b,ask_b,0,0,0,0)
        center_a=(a['requested_at']+a['received_at'])/2;center_b=(b['requested_at']+b['received_at'])/2
        fresh = max(now-a['received_at'],now-b['received_at']) <= 12 and abs(center_a-center_b) <= 3 and max(a['latency_ms'],b['latency_ms']) <= 3000
        mid_current=spread_bp((bid_a+ask_a)/2,(bid_b+ask_b)/2)
        median=s['median_bp']
        s.update({'a':a['venue'],'b':b['venue'],'name_a':name_a,'name_b':name_b, 'market_a':a['market'],'market_b':b['market'], 'quote':a['quote'],
                  'current_mid_bp':mid_current,'deviation_bp':mid_current-median if median is not None else None,
                  'depth_ok':depth,'common_qty':common_qty,'fresh':fresh,'observation_skew_ms':round(abs(center_a-center_b)*1000),
                  'live_samples':live['samples'],'live_median_bp':live['median_bp'],'live_history_ready':live['history_ready'],
                  'median_kind':'已收盘1分钟成交K线估算','live_kind':'固定每分钟一次盘口中间价采样；非逐笔盘口中位数'})
        for r in s['directions']:
            buy_leg = a if r['buy']=='a' else b
            sell_leg = b if r['sell']=='b' else a
            r['buy_venue']=name_a if r['buy']=='a' else name_b
            r['sell_venue']=name_b if r['sell']=='b' else name_a
            r['buy_market']=buy_leg['market'];r['sell_market']=sell_leg['market']
            r['risk_note']=''
            if sell_leg['market']=='spot':
                r['risk_note']='卖现货须已有库存，或先核验可借币额度、借币利率和归还条件；无库存不能直接做空。转币费用与到账风险未自动计入。'
            if buy_leg['market']=='perp' or sell_leg['market']=='perp':
                r['risk_note']+=' 永续须另核对资金费方向、结算时点、保证金与强平风险；现货持仓不能防止另一平台的保证金强平。'
            if not depth:r['status']='盘口深度不足'
            elif not fresh:r['status']='报价过期或不同步'
            elif not s['history_ready']:r['status']='历史不足，不作判断'
            elif r['net_space_bp'] < 0:r['status']='费用后无空间'
            elif r['net_space_bp'] < threshold:r['status']='剩余空间低于门槛'
            else:r['status']='估算有空间，需人工复核'
        s['directions'].sort(key=lambda r:r['net_space_bp'],reverse=True)
        pairs.append(s)
    pairs.sort(key=lambda s:s['directions'][0]['net_space_bp'] if s['directions'] else -1e9,reverse=True)
    quotes=[{'venue':q['venue'],'name':q['name'],'market':q['market'],'symbol':q['symbol'],'quote':q['quote'],'bid':q['bids'][0][0],'ask':q['asks'][0][0], 'latency_ms':q['latency_ms'],'received_at':q['received_at'],'source_timestamp_present':bool(q['source_ts']), 'history_count':len(q['candles']),'history_error':q['history_error'],'meta':q['meta']} for q in d['quotes']]
    return {'symbol':symbol,'market':market,'notional':notional,'fetched_at':d['fetched_at'],'end':d['end'],'pairs':pairs,'quotes':quotes,'errors':d['errors'],'skipped':skipped,'sampling_active':(symbol,market) in ACTIVE,'read_only':True,'warnings':['净空间是回归情景估算，不是锁定收益或开仓建议。','K线收盘价不是历史可成交盘口；盘口按每分钟一次采样，未满2小时明确标注。','费用按两边开平共4笔计算；已计入当前指定金额的盘口深度。资金费/借币费、平仓价差和延迟损失需自行预留，未来中枢可能漂移。','代码相同不保证经济标的一致。尤其股票、指数、映射代币须另核验合约乘数、结算和交易时段。']}


def sampler():
    while True:
        time.sleep(30)
        now=time.time()
        with LOCK:
            work=[(k,dict(v)) for k,v in ACTIVE.items() if v['until']>now]
            for k in list(ACTIVE):
                if ACTIVE[k]['until']<=now:del ACTIVE[k]
        for (symbol,market),v in work:
            try:
                d=collect(symbol,market,v['venues'])
                with LOCK:CACHE[(symbol,market,tuple(sorted(v['venues'])))]=d
            except Exception:pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def respond(self,status,body,content_type='application/json; charset=utf-8'):
        if isinstance(body,dict):body=json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'");self.end_headers();self.wfile.write(body)
    def do_GET(self):
        u=urlsplit(self.path)
        if len(self.path)>2500:return self.respond(414,{'error':'请求过长'})
        if u.path=='/health':return self.respond(200,{'ok':True,'read_only':True,'version':'1.1.0'})
        if u.path=='/api/scan':
            try:return self.respond(200,analyze(parse_qs(u.query)))
            except ValueError as e:return self.respond(400,{'error':str(e)})
            except Exception:return self.respond(503,{'error':'行情查询失败，请稍后重试'})
        static={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
        if u.path in static:
            name,kind=static[u.path];return self.respond(200,(ROOT/'static'/name).read_bytes(),kind)
        return self.respond(404,{'error':'Not found'})
    def do_POST(self):return self.respond(405,{'error':'只读服务，不提供写入或交易接口'})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8901);args=p.parse_args()
    with connect():pass
    threading.Thread(target=sampler,daemon=True).start()
    print('Basis Radar read-only server listening',flush=True)
    ThreadingHTTPServer((args.host,args.port),Handler).serve_forever()
