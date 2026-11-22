"""Redesigned dual-accounting (BNB-denominated + USDT-denominated) BNCB/BNB LP dashboard.

Preserves exact on-chain ledger math from bncb_fast.py and chart/import/export hooks from bncb_chart.js.
No client RPC scans, no private keys, no wallet address displayed on screen.
"""
import base64
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path


def fmt(n, k=2):
    return f"{n:,.{k}f}"


def sfmt(n, k=2):
    return f"{n:+,.{k}f}"


def clock(t):
    return datetime.fromtimestamp(t, timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def render(h, x, warn, template=None):
    capB = float(h["capitalB"])
    capU = float(h["capitalU"])
    U = float(x["U"])
    px = float(x["px"])
    pxB = px / U if U > 0 else 0.0

    a = float(x["a"])          # 仓内剩余 BNCB
    z = float(x["z"])          # 仓内剩余 BNB
    a_bnb = a * pxB            # 剩余 BNCB 按池子现价折合 BNB
    feeB = float(x["feeB"])    # 本轮未领手续费折合 BNB
    feeU = float(x["fee"])
    carryB = float(h["carryB"])  # 第一轮已领回款 BNB
    liveB = float(x["current"])  # 当前仓位 + 未领费 (BNB)
    netB = float(x["net"])       # 两轮合计净值 (BNB)
    netU = float(x["netU"])      # 两轮合计净值 (U)

    # 1. BNB 币本位核算（剔除 BNB 自身涨跌）
    pnlB = netB - capB
    pnlB_pct = (pnlB / capB * 100.0) if capB > 0 else 0.0
    pnlB_u = pnlB * U

    # 估算现在立刻把全部 BNCB 市价 swap 成 BNB（扣 0.25% 池子交易费）
    swap_fee_b = a_bnb * 0.0025
    exit_netB = netB - swap_fee_b
    exit_pnlB = exit_netB - capB
    exit_pnlB_pct = (exit_pnlB / capB * 100.0) if capB > 0 else 0.0

    # 2. U 本位核算 & 归因拆解（U总盈亏 = BNB币价涨跌影响 + 做LP币本位盈亏折U）
    pnlU = netU - capU
    pnlU_pct = (pnlU / capU * 100.0) if capU > 0 else 0.0
    avg_bnb_cost = (capU / capB) if capB > 0 else U
    bnb_drop_u = pnlU - pnlB_u  # 精确等于 capB * (U - avg_bnb_cost)

    # 3. 分轮次表现
    old_in = float(h["oldIn"])
    old_out = float(h["oldOut"])
    old_pnlB = float(h["oldPnlB"])
    old_pnlU = float(h["oldPnlU"])
    r2_cost = float(h["newCost"])
    r2_pnlB = liveB - r2_cost
    r2_pnlB_pct = (r2_pnlB / r2_cost * 100.0) if r2_cost > 0 else 0.0
    r2_pnlU = r2_pnlB * U

    # 4. 价格区间位置
    lo, hi = float(x["lo"]), float(x["hi"])
    in_range = bool(x["inRange"])
    pos_pct = max(0.0, min(100.0, 100.0 * (px - lo) / (hi - lo))) if hi > lo else 50.0
    age_s = max(0, int(time.time() - x["time"]))
    warning = warn or ("快照超过 3 分钟，后台正在自动刷新链上数据…" if age_s > 180 else "")

    cls = lambda v: "pos" if v > 1e-9 else ("neg" if v < -1e-9 else "mut")
    verdict_bnb = "币本位盈利" if pnlB >= 0 else "币本位亏损"
    verdict_u = "U 本位盈利" if pnlU >= 0 else "U 本位亏损"

    warn_html = f'<div id="msg" class="alert">{escape(warning)}</div>' if warning else '<div id="msg" class="alert" style="display:none"></div>'

    html = f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BNCB / BNB LP · 双本位盈亏看板</title>
<style>
:root{{
  --bg:#0b0f17;--card:#121824;--card2:#0e1420;--bd:#232c3d;--tx:#e6edf3;--mut:#8b949e;
  --g:#3fb950;--r:#f85149;--y:#d29922;--b:#58a6ff;
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--tx);font:14px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1180px;margin:0 auto;padding:14px 16px 36px}}
.topbar{{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px;background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:12px 16px}}
.brand{{display:flex;flex-wrap:wrap;align-items:center;gap:10px}}
.brand h1{{font-size:18px;margin:0}}
.pill{{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:600;border:1px solid var(--bd);background:var(--card2)}}
.pill.ok{{background:#0f2a1a;border-color:#2ea043;color:var(--g)}}
.pill.out{{background:#3d1214;border-color:var(--r);color:var(--r)}}
.actions{{display:flex;align-items:center;gap:8px}}
button{{background:#1b2434;color:var(--tx);border:1px solid var(--bd);border-radius:8px;padding:6px 12px;cursor:pointer;font-size:12.5px;font-weight:600}}
button:hover{{border-color:var(--b);background:#222e42}}
.alert{{margin-top:10px;background:#2d2208;border:1px solid #9e6a03;color:#e3b341;border-radius:10px;padding:9px 14px;font-size:13px}}
.hero{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px}}
@media(max-width:860px){{.hero{{grid-template-columns:1fr}}}}
.card{{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:15px 17px}}
.card.primary-bnb{{border-color:#264a78;background:linear-gradient(160deg,#121d2e,#121824)}}
.card h2{{font-size:14px;margin:0 0 10px;color:#c9d1d9;display:flex;align-items:center;justify-content:space-between;font-weight:600}}
.big{{font-size:30px;font-weight:800;letter-spacing:-.5px;font-variant-numeric:tabular-nums;margin:4px 0}}
.subbig{{font-size:13px;color:#a9b4c4;margin-bottom:10px}}
.pos{{color:var(--g)}}.neg{{color:var(--r)}}.yel{{color:var(--y)}}.blu{{color:var(--b)}}.mut{{color:var(--mut)}}
.split{{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}}
.mini{{background:var(--card2);border:1px solid var(--bd);border-radius:9px;padding:9px 11px}}
.mini .k{{font-size:11.5px;color:var(--mut)}}
.mini .v{{font-size:16px;font-weight:700;margin-top:3px;font-variant-numeric:tabular-nums}}
.mini .s{{font-size:11.5px;color:var(--mut);margin-top:2px}}
.grid3{{display:grid;grid-template-columns:1.35fr 1fr 1fr;gap:12px;margin-top:12px}}
@media(max-width:960px){{.grid3{{grid-template-columns:1fr}}}}
.swap-list{{display:flex;flex-direction:column;gap:6px;margin-top:4px}}
.swap-row{{display:flex;justify-content:space-between;align-items:baseline;font-size:13px;padding:5px 0;border-bottom:1px dashed #1e2736}}
.swap-row:last-child{{border-bottom:0}}
.swap-row.total{{border-top:1px solid var(--bd);border-bottom:0;padding-top:8px;margin-top:2px;font-weight:700;font-size:14px}}
.rbar{{margin:14px 0 10px}}
.track{{height:8px;background:#1c2433;border-radius:99px;position:relative}}
.rdot{{position:absolute;top:50%;width:14px;height:14px;border-radius:50%;transform:translate(-50%,-50%);border:2px solid #fff}}
.rlbl{{display:flex;justify-content:space-between;font-size:12px;color:var(--mut);margin-top:6px;font-variant-numeric:tabular-nums}}
.mid2{{display:grid;grid-template-columns:1.15fr 1fr;gap:12px;margin-top:12px}}
@media(max-width:960px){{.mid2{{grid-template-columns:1fr}}}}
.chart{{width:100%;height:auto;display:block}}
.chart text{{fill:#8b949e}}
table{{width:100%;border-collapse:collapse;font-size:12.5px}}
th,td{{padding:8px 8px;border-bottom:1px solid #1c2433;text-align:right}}
th:first-child,td:first-child{{text-align:left}}
th{{color:var(--mut);font-weight:600}}
details{{margin-top:12px;background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:10px 16px}}
summary{{cursor:pointer;font-size:12.5px;color:var(--b);user-select:none}}
.s{{font-size:12px;color:var(--mut)}}
a{{color:var(--b);text-decoration:none;cursor:pointer}}
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div class="brand">
      <h1>BNCB / BNB 流动性（LP）双本位看板</h1>
      <span class="pill {'ok' if in_range else 'out'}">{'● 区间内赚费中' if in_range else '● 已出区间'}</span>
      <span class="s">区块 {x['block']:,} ｜ 更新于 <span id="lastUpdate">{clock(x['time'])}</span>（每分钟刷新）</span>
    </div>
    <div class="actions">
      <button id="btnRefresh" type="button">立即刷新</button>
    </div>
  </div>
  {warn_html}

  <!-- 首屏两大核心对比：BNB 币本位 vs U 本位（含归因） -->
  <div class="hero">
    <div class="card primary-bnb">
      <h2><span>BNB 币本位净盈亏（剔除 BNB 涨跌）</span><span class="pill">{verdict_bnb}</span></h2>
      <div class="big {cls(pnlB)}">{sfmt(pnlB, 5)} BNB <span style="font-size:18px">({sfmt(pnlB_pct, 2)}%)</span></div>
      <div class="subbig">累计投入 <b>{capB:.5f} BNB</b> → 全部折回现价 <b>{netB:.5f} BNB</b>（折合 <span class="{cls(pnlB_u)}">{sfmt(pnlB_u, 2)} U</span>）</div>
      <div class="split">
        <div class="mini">
          <div class="k">若现在立刻 Swap 换回 BNB（扣 0.25% 费）</div>
          <div class="v {cls(exit_pnlB)}">{exit_netB:.5f} BNB</div>
          <div class="s">币本位净盈亏 {sfmt(exit_pnlB, 5)} BNB（{sfmt(exit_pnlB_pct, 2)}%）</div>
        </div>
        <div class="mini">
          <div class="k">对比：一直拿着 {capB:.4f} BNB 不做 LP</div>
          <div class="v {cls(pnlB)}">{'多赚' if pnlB >= 0 else '少拿'} {abs(pnlB):.5f} BNB</div>
          <div class="s">即「手续费收入 − 无常损失」净结果</div>
        </div>
      </div>
    </div>

    <div class="card">
      <h2><span>USDT（U）本位总盈亏 & 归因拆解</span><span class="pill">{verdict_u}</span></h2>
      <div class="big {cls(pnlU)}">{sfmt(pnlU, 2)} U <span style="font-size:18px">({sfmt(pnlU_pct, 2)}%)</span></div>
      <div class="subbig">投入当时总值 <b>{fmt(capU)} U</b> → 当前总净值 <b>{fmt(netU)} U</b>（BNB 均价 ${fmt(avg_bnb_cost)} → 现价 ${fmt(U)}）</div>
      <div class="split">
        <div class="mini">
          <div class="k">① BNB 自身涨跌导致（拿现货也会亏/赚）</div>
          <div class="v {cls(bnb_drop_u)}">{sfmt(bnb_drop_u, 2)} U</div>
          <div class="s">{capB:.4f} BNB × ({sfmt(U - avg_bnb_cost, 2)} U/BNB)</div>
        </div>
        <div class="mini">
          <div class="k">② 做 LP 自身币本位盈亏折 U</div>
          <div class="v {cls(pnlB_u)}">{sfmt(pnlB_u, 2)} U</div>
          <div class="s">{sfmt(pnlB, 5)} BNB × ${fmt(U)}（① + ② = 总盈亏）</div>
        </div>
      </div>
    </div>
  </div>

  <!-- 第二排：剩余 BNCB 换回 BNB 算账清单 + 价格区间 + 本轮手续费年化 -->
  <div class="grid3">
    <div class="card">
      <h2><span>全部资产折算成 BNB 明细（怎么算出来的）</span><span class="s">1 BNCB = {pxB:.6f} BNB</span></h2>
      <div class="swap-list">
        <div class="swap-row"><span>① 仓内剩余 BNCB（<b>{fmt(a, 2)}</b> 枚）按现价换 BNB</span><span><b>{a_bnb:.5f} BNB</b></span></div>
        <div class="swap-row"><span>② 仓内剩余 BNB</span><span><b>{z:.5f} BNB</b></span></div>
        <div class="swap-row"><span>③ 本轮未领取手续费（约 {fmt(feeU)} U）</span><span class="pos">+{feeB:.5f} BNB</span></div>
        <div class="swap-row"><span>④ 第一轮已领到钱包回款</span><span class="pos">+{carryB:.5f} BNB</span></div>
        <div class="swap-row total"><span>合计全部折合 BNB（对比投入 {capB:.5f} BNB）</span><span class="blu">{netB:.5f} BNB</span></div>
        <div class="swap-row"><span class="s">扣除估算 0.25% Swap 交易费（-{swap_fee_b:.5f} BNB）后净到手</span><span class="s">≈ {exit_netB:.5f} BNB</span></div>
      </div>
    </div>

    <div class="card">
      <h2><span>当前价格与 LP 区间</span><span class="{'pos' if in_range else 'neg'}">{'区间内' if in_range else '区间外'}</span></h2>
      <div class="big" style="font-size:24px">${px:.4f} <span class="s">U/BNCB</span></div>
      <div class="s">当前 BNB 价格：<b>${fmt(U)} U</b> ｜ 池子汇率：{pxB:.6f} BNB/BNCB</div>
      <div class="rbar">
        <div class="track"><i class="rdot" style="left:{pos_pct:.2f}%;background:{'#3fb950' if in_range else '#f85149'}"></i></div>
        <div class="rlbl"><span>下限 ${lo:.4f}</span><span>当前位置 {pos_pct:.0f}%</span><span>上限 ${hi:.4f}</span></div>
      </div>
      <div class="s">越靠近下限，仓内 BNCB 越多、BNB 越少；跌破下限将变为 100% BNCB 停止赚费。</div>
    </div>

    <div class="card">
      <h2><span>第二轮（当前仓位）实时表现</span><span class="s">含未领手续费</span></h2>
      <div class="big {cls(r2_pnlB)}" style="font-size:24px">{sfmt(r2_pnlB, 5)} BNB</div>
      <div class="s">第二轮币本位盈亏（{sfmt(r2_pnlB_pct, 2)}%，折 {sfmt(r2_pnlU, 2)} U）</div>
      <div class="split">
        <div class="mini">
          <div class="k">本轮未领手续费</div>
          <div class="v pos">{fmt(feeU)} U</div>
          <div class="s">{feeB:.5f} BNB</div>
        </div>
        <div class="mini">
          <div class="k">本轮手续费年化</div>
          <div class="v blu">{x['apr']:.1f}%</div>
          <div class="s">按开仓至今简单年化</div>
        </div>
      </div>
    </div>
  </div>

  <!-- 第三排：两轮净值曲线 + 分轮次对账明细 -->
  <div class="mid2">
    <div class="card">
      <h2><span>两轮连续 BNB 净值走势</span><span class="s">红线=净值 / 虚线=累计投入 BNB</span></h2>
      <div id="chartNav" class="s">图表加载中…</div>
    </div>

    <div class="card">
      <h2><span>两轮完整对账明细（已含 Gas {h['gasB']:.5f} BNB）</span><span class="s">不重复计本金</span></h2>
      <table>
        <thead><tr><th>轮次</th><th>投入 / 成本</th><th>当前 / 撤出折合</th><th>BNB 币本位盈亏</th></tr></thead>
        <tbody>
          <tr>
            <td><b>第一轮</b><div class="s">已撤出</div></td>
            <td>{old_in:.5f} BNB</td>
            <td>{old_out:.5f} BNB</td>
            <td class="{cls(old_pnlB)}"><b>{sfmt(old_pnlB, 5)} BNB</b><div class="s">{sfmt(old_pnlU, 2)} U</div></td>
          </tr>
          <tr>
            <td><b>第二轮</b><div class="s">持有中</div></td>
            <td>{r2_cost:.5f} BNB<div class="s">滚入 {fmt(h['carry0'], 2)} BNCB + 新配 {h['newB']:.4f} BNB</div></td>
            <td>{liveB:.5f} BNB<div class="s">含未领费 {feeB:.5f} BNB</div></td>
            <td class="{cls(r2_pnlB)}"><b>{sfmt(r2_pnlB, 5)} BNB</b><div class="s">{sfmt(r2_pnlU, 2)} U</div></td>
          </tr>
          <tr style="font-weight:700;background:#0e1420">
            <td>两轮合计</td>
            <td>{capB:.5f} BNB<div class="s">{fmt(capU)} U</div></td>
            <td>{netB:.5f} BNB<div class="s">{fmt(netU)} U</div></td>
            <td class="{cls(pnlB)}">{sfmt(pnlB, 5)} BNB<div class="s">U本位 {sfmt(pnlU, 2)} U</div></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <details>
    <summary>工具：刷新快照 / 历史曲线导入导出（展开时暂停自动刷新）</summary>
    <div style="display:flex;flex-wrap:wrap;gap:14px;margin-top:10px;font-size:13px">
      <a id="lnkRescan">重新拉取快照</a>
      <a id="lnkExport">导出本地净值曲线 JSON</a>
      <a id="lnkImport">导入历史曲线 JSON</a>
      <input id="fileImport" type="file" accept="application/json" style="display:none">
    </div>
  </details>
</div>
'''
    data = dict(
        first=h["firstTs"], firstNav=h["firstNav"], owner=h["owner"],
        open=h["openTs"], closed=h.get("closeTs"), old=h["oldOut"], oldIn=h["oldIn"],
        capital=h["capitalB"], carry=carryB, block=x["block"], now=x["time"],
        net=x["net"], fee=x["feeB"], cost=h["newCost"],
    )
    script = "const S=" + json.dumps(data, separators=(",", ":")) + ";\n" + Path(__file__).with_name("bncb_chart.js").read_text()
    dig = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    return (html + "<script>" + script + "</script></body></html>").encode("utf-8"), dig
