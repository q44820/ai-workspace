"""Pinned-block read-only valuation of a reconciled two-round LP ledger. No signer or model."""
import json, math, os, time, threading, urllib.request, concurrent.futures
from decimal import Decimal as D, localcontext
from pathlib import Path
from datetime import datetime, timezone, timedelta
from html import escape
ROOT=Path(__file__).resolve().parent/'private-pages';LOCK=threading.Lock();RETRY=0
RPC=('https://bsc-rpc.publicnode.com','https://bsc-dataseed.bnbchain.org','https://56.rpc.thirdweb.com');E=D(10)**18

def fetch(m,p):
    for url in RPC:
        try:
            req=urllib.request.Request(url,data=json.dumps(dict(jsonrpc='2.0',id=1,method=m,params=p)).encode(),headers={'Content-Type':'application/json','User-Agent':'LP-readonly/1'})
            with urllib.request.urlopen(req,timeout=5) as r:return json.load(r)['result']
        except (OSError,KeyError,ValueError):pass
    raise ValueError('public reads unavailable')

def snapshot(h):
    head=int(fetch('eth_blockNumber',[]),16)-5;b=hex(head);pad=lambda n:format(n,'064x');owner=h['owner'];id_=pad(h['id']);npm=h['npm']
    def call(a,sel,fr=None):
        tx={'to':a,'data':sel};tx.update({'from':fr} if fr else {});v=fetch('eth_call',[tx,b]);return [int(v[i:i+64],16) for i in range(2,len(v),64)]
    jobs=[lambda:call(npm,'0x99fbab88'+id_),lambda:call(npm,'0x6352211e'+id_),lambda:call(h['pool'],'0x3850c7bd'),lambda:call(h['usdPool'],'0x3850c7bd'),lambda:call(npm,'0xfc6f7865'+id_+pad(int(owner,16))+pad(2**128-1)*2,owner),lambda:int(fetch('eth_getTransactionCount',[owner,b]),16),lambda:int(fetch('eth_getBlockByNumber',[b,False])['timestamp'],16)]
    with concurrent.futures.ThreadPoolExecutor(7) as ex:w,o,p,u,fees,n,ts=list(ex.map(lambda f:f(),jobs))
    if not (o[0]==int(owner,16) and w[7]==int(h['L']) and n==h['auditedNonce']):raise ValueError('ledger needs reconciliation')
    with localcontext() as c:
        c.prec=65; q=D(p[0])/2**96;lo=D('1.0001')**(D(h['lo'])/2);hi=D('1.0001')**(D(h['hi'])/2);t=min(hi,max(q,lo));L=D(w[7])/E;a=L*(hi-t)/(t*hi);z=L*(t-lo);P=q*q;U=(D(2**96)/u[0])**2;F=(D(fees[0])*P+fees[1])/E;live=a*P+z+F;nav=live+D(str(h['carryB']));
        return dict(block=head,time=ts,current=float(live),net=float(nav),netU=float(nav*U),pnl=float(nav*U-D(str(h['capitalU']))),pnlB=float(nav-D(str(h['capitalB']))),pnlVsU=float((nav-D(str(h['capitalB'])))*U),fee=float(F*U),feeB=float(F),a=float(a),z=float(z),px=float(P*U),lo=float(lo*lo*U),hi=float(hi*hi*U),inRange=lo<=q<hi,apr=float(F/D(str(h['newCost']))*365*86400/(ts-h['openTs'])*100),U=float(U))

def refresh():
    global RETRY
    try:
        h=json.loads((ROOT/'bncb-ledger.json').read_text());x=snapshot(h);f=ROOT/'bncb-snapshot.next';f.write_text(json.dumps(x));os.replace(f,ROOT/'bncb-snapshot.json');(ROOT/'bncb-warning').unlink(missing_ok=True)
    except Exception as e:
        (ROOT/'bncb-warning').write_text('流水有变，需重新对账' if 'reconciliation' in str(e) else '节点读取失败')
    finally:RETRY=time.monotonic()+30;LOCK.release()

def page(h,x,warning=''):
    f=lambda k,n=2:f'{x[k]:,.{n}f}';ts=datetime.fromtimestamp(x['time'],timezone(timedelta(hours=8))).strftime('%m-%d %H:%M:%S');age=max(0,int(time.time()-x['time']));lbl='历史快照，非实时' if warning or age>180 else '已核快照';parts=[('两轮累计净盈亏（含手续费和 Gas）',f"{f('pnl')} U",f"累计投入 {h['capitalU']:,.2f} U → 净值 {f('netU')} U；每笔投入按当时 BNB/U 折算。"),('相对一直持有投入的 BNB',f"{f('pnlB',5)} BNB",f"按本次快照折 {f('pnlVsU')} U；与上方 U 计净盈亏口径不同。"),('第一轮 · 已撤出',f"{h['oldPnlU']:,.2f} U",f"撤出时按市价核算（代币未卖）；相对原始投入 {h['oldPnlB']:+.5f} BNB。领费已计入。"),('第二轮 · 当前仓位',f"{f('current',5)} BNB",f"仓内 {f('a')} BNCB + {f('z',5)} BNB；未领费 {f('fee')} U 已含在价值内；本轮实际手续费简单年化 {f('apr',1)}%。"),('重组与成本',f"追加 {h['newB']:.5f} BNB",f"{h['carry0']:,.6f} BNCB 全部滚入，不重复计本金。前轮已领 {h['carryB']:.6f} BNB 作为策略回款保留，以持有 BNB 计价；两轮已计 Gas {h['gasU']:.3f} U。"),('当前区间 · '+('区间内' if x['inRange'] else '区间外'),f"{f('px',4)} U/BNCB",f"{f('lo',4)} – {f('hi',4)} U/BNCB")];return ('<!doctype html><html lang="zh-CN"><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><meta http-equiv=refresh content="60"><title>我的 BNCB / BNB LP</title><style>body{font:16px system-ui;color:#172332;background:#f4f6f9;max-width:760px;margin:auto;padding:16px}section{background:white;padding:18px;margin:12px 0;border-radius:14px}strong{display:block;font-size:26px;margin:5px 0}small{color:#465567}a{color:#1459a5}</style><h1>我的 BNCB / BNB LP</h1><p>两轮连续核算 · '+lbl+' · 北京时间 '+ts+' · 区块 '+str(x['block'])+'<br>'+escape(warning)+'</p>'+''.join('<section>'+a+'<strong>'+b+'</strong><small>'+c+'</small></section>' for a,b,c in parts)+'<p>页面先显示日本服务器最近一次核算；打开时后台更新，每分钟重载。<a href="?detail=1">本轮详细图表、24h 年化及历史导入导出</a></p></html>').encode()

def serve(handler,head):
    h=json.loads((ROOT/'bncb-ledger.json').read_text());x=json.loads((ROOT/'bncb-snapshot.json').read_text());warn=ROOT/'bncb-warning';warning=warn.read_text() if warn.exists() else ''
    if time.time()-x['time']>55 and time.monotonic()>RETRY and LOCK.acquire(False):threading.Thread(target=refresh,daemon=True).start()
    body=page(h,x,warning);handler.send_response(200)
    for k,v in {'Content-Type':'text/html; charset=utf-8','Content-Length':str(len(body)),'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Frame-Options':'DENY','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"}.items():handler.send_header(k,v)
    handler.end_headers()
    if not head:handler.wfile.write(body)
