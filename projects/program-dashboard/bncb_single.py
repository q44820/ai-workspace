"""Single server-rendered LP view in the original card layout; no client RPC scans."""
import json,re,base64,hashlib,time
from html import escape
from datetime import datetime,timezone,timedelta
from pathlib import Path
from bncb_fast import ROOT

def fmt(n,k=2):return f'{n:,.{k}f}'
def clock(t):return datetime.fromtimestamp(t,timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')

def render(h,x,warn,template):
    base=template.split('<script id="core">',1)[0]; assert 'id="chartNav"' in base
    base=base.replace('开仓时 → 当前价值','两轮累计投入 → 当前净值').replace('手续费年化（24h）','当前仓位 · 手续费年化').replace('开仓明细（逐笔）','两次加 LP 明细（同页展开）').replace('每天一个点','两轮连续 · 实际观测点').replace('每 10 分钟自动刷新','每分钟更新').replace('重新扫描','刷新快照')
    base=re.sub(r'<input id="addr"[^>]*>', '<span>两轮连续核算</span>',base)
    base=base.replace('对比：不做 LP、一直拿着 BNB','两轮累计净盈亏 · 按快照价折 U')
    c=h['capitalB']; net=x['net']; b=net-c; pnl=b*x['U'];carry=h['carryB'];left=x['lo'];right=x['hi'];pos=max(0,min(100,100*(x['px']-left)/(right-left)));dotcolor='#16a34a' if x['inRange'] else '#dc2626';tag='区间内' if x['inRange'] else '区间外'
    one=lambda v,txt:'<span class="'+('neg' if v<0 else 'pos')+'">'+txt+'</span>'
    vals={'mVal':f'{c:.5f} → {net:.5f} BNB','mValS':'投入含 Gas；净值含已领及未领手续费。','mPnl':one(pnl,f'{pnl:+,.2f} U'),'mPnlS':f'{b:+.5f} BNB（{b/c*100:+.2f}%）× 快照价 {x["U"]:,.2f} U/BNB。只折算盈亏，不计 BNB 自身涨跌。','mApr':f'{x["apr"]:.1f}%','mAprS':'本轮自开仓以来的实际手续费简单年化，不是两轮收益率。<span id="apr24"></span>','mFee':f'{fmt(x["fee"])} U','mFeeS':f'本轮未领 {x["feeB"]:.6f} BNB，已计入净值，不再另加。','mRange':tag,'mRangeS':f'<div class="rbar"><div class="track"><i class="dot" style="left:{pos:.2f}%;background:{dotcolor}"></i></div></div>{left:.4f} – {right:.4f} U/BNCB；现价 {x["px"]:.4f} U/BNCB','mHold':f'{fmt(x["a"])} BNCB + {fmt(x["z"],4)} BNB','mHoldS':'当前仓内数量，不含未领手续费。','costLine':f'首轮撤出时盈亏 {h["oldPnlB"]:+.5f} BNB（已含在累计数内）；第二轮另配 {h["newB"]:.5f} BNB。两轮 Gas 合计 {h["gasB"]:.6f} BNB。','lastUpdate':clock(x['time'])}
    for k,v in vals.items():base,n=re.subn(r'(id="'+k+r'">)[^<]*(</)',lambda m:m[1]+v+m[2],base,count=1);assert n==1
    base=base.replace('<tbody></tbody>',f'<tbody><tr><td>第一轮 · 已撤</td><td>投入及费用 {h["oldIn"]:.5f} BNB；撤出及已领折 {h["oldOut"]:.5f} BNB。按撤出时市价核算，不代表卖出。</td></tr><tr><td>第二轮 · 持有中</td><td>{h["carry0"]:,.6f} BNCB 滚入，不重复计本金；另配入 {h["newB"]:.5f} BNB。前轮已领的 {carry:.6f} BNB 按保留 BNB 计入。</td></tr></tbody>');warning=warn or ('快照较旧，后台正在核实；不是实时值。' if time.time()-x['time']>180 else '')
    if warning:base=base.replace('<div id="msg" class="msg"></div>','<div id="msg" class="msg" style="display:block">'+escape(warning)+'</div>')
    data=dict(first=h['firstTs'],firstNav=h['firstNav'],owner=h['owner'],open=h['openTs'],closed=h.get('closeTs'),old=h['oldOut'],oldIn=h['oldIn'],capital=h['capitalB'],carry=carry,block=x['block'],now=x['time'],net=x['net'],fee=x['feeB'],cost=h['newCost']);script='const S='+json.dumps(data,separators=(',',':'))+';\n'+Path(__file__).with_name('bncb_chart.js').read_text();dig=base64.b64encode(hashlib.sha256(script.encode()).digest()).decode();return (base+'<script>'+script+'</script></body></html>').encode(),dig
