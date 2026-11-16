"""离线模拟测试（DRY_RUN，不连交易账户、不发单）。用脚本化盘口驱动 tick()，覆盖：
A 盘口瞬间变宽（脚本化场景）  B 短暂偏离不足 confirmSec  C 正常开平（最短持仓 30s、真实成交价算收益）
D 下单前复核失败  E 止损立即执行  F 入场偏离偏小：回归但利润不够就继续持有，有利润再平  G 强平保护立即执行  H 一键全平
用法：python test_offline.py（临时目录、禁网络）"""
import os, sys, asyncio, json, tempfile
_TEST_TMP = tempfile.TemporaryDirectory(prefix="anth-offline-")
os.environ.update(DRY_RUN="1", LIVE_ENABLED="1", ANTH_OFFLINE_SIM="1", HL_ACCOUNT_ADDRESS="", LIGHTER_ACCOUNT_INDEX="", STATUS_PUSH_URL="", PUBLIC_PATH="",
                  STATE_FILE=str(__import__("pathlib").Path(_TEST_TMP.name) / "state.json"), LOG_FILE=str(__import__("pathlib").Path(_TEST_TMP.name) / "sim.log"),
                  DRY_CASH_IO="100.0", DRY_CASH_LT="100")
import bot
bot.LEVERAGE = 3  # Synthetic stress-test assumption only; not production configuration.
CLK = [1_800_000_000_000]
bot.now_ms = lambda: CLK[0]
B, S, V = bot.B, bot.S, bot.V
MED = 36.8
FAIL = []
def check(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond: FAIL.append(msg)

def setbook(iob, ioa, ltb, lta, sz=5.0):
    B.io = dict(bids=[(iob, sz), (iob - 0.5, sz)], asks=[(ioa, sz), (ioa + 0.5, sz)], t=CLK[0])
    B.lt_b = {f"{ltb:.1f}": sz, f"{ltb - 0.5:.1f}": sz}; B.lt_a = {f"{lta:.1f}": sz, f"{lta + 0.5:.1f}": sz}; B.lt_t = CLK[0]

def reset():
    S.mode = "live"; S.pos = None; S.trades = []; S.alert = None; S.cool = 0; S.busy = False
    S.cfg.update(margin=50, maxNotional=50, lev=1, openBp=25, confirmSec=2, minProfitBp=3, minHoldSec=30, cooldownSec=60, feeIo=0.009, feeLt=0.028)
    t = CLK[0]; S.buckets = {k: MED for k in range((t - 125 * 60000) // 60000 * 60000, t - 60000, 60000)}
    bot.DRY_SLIP.update(io=0.0, lt=0.0); bot.CONF.update(dir=None, n=0)
    bot.BAL["io"] = dict(usdc=100.0, hold=0, pos=0, liq=None, t=CLK[0]); bot.BAL["lt"] = dict(collateral=105, avail=105, pos=0, liq=None, t=CLK[0])
    bot.DRY_CASH.update(io=100.0, lt=100.0)

async def run_for(sec, book=None, dt=0.25):
    """每 dt 秒一个 tick，持续 sec 秒；book 可以是函数(第几秒)->盘口"""
    steps = int(round(sec / dt))
    for i in range(steps):
        CLK[0] += int(dt * 1000)
        if book: setbook(*book(i * dt))
        else: B.io["t"] = CLK[0]; B.lt_t = CLK[0]
        S.buckets = {k: MED for k in S.buckets}  # 中位数保持不变
        for k in list(S.buckets): pass
        bot.BAL["io"]["t"] = bot.BAL["lt"]["t"] = CLK[0]
        await bot.tick(CLK[0])
        S.buckets[CLK[0] // 60000 * 60000] = MED

NORMAL = (1772.4, 1772.8, 1766.0, 1766.4)            # 平时：中间价价差 ≈ +36.7bp
RICH = (1778.4, 1778.8, 1766.0, 1766.4)              # io 真贵：可成交价差 1778.4/1766.4 = +67.9bp（偏离 +31）

async def main():
    await V.init()
    # ---------- A ----------
    print("A 脚本化场景：io 卖一瞬间变宽（中间价价差 +65bp，可成交只有 +27.7bp）")
    reset(); setbook(*NORMAL); await run_for(1)
    await run_for(5, lambda s: (1771.5, 1784.0, 1765.8, 1766.6))
    check(bot.LIVE["dev"] >= 25, f"旧规则（中间价）偏离 {bot.LIVE['dev']:+.1f}bp 会开仓")
    check(S.pos is None and not S.trades, f"新规则（可成交价）偏离 {bot.LIVE['xDevS']:+.1f}bp，不开仓")
    # ---------- B ----------
    print("B 真实偏离只持续 0.75 秒（3 个 tick）")
    reset(); setbook(*NORMAL); await run_for(1)
    await run_for(0.75, lambda s: RICH); await run_for(3, lambda s: NORMAL)
    check(S.pos is None, "没满 confirmSec=2s，不开仓")
    # ---------- C ----------
    print("C 偏离持续 2.5 秒 → 开仓；10 秒回归但最短持仓未到；31 秒后平仓")
    reset(); setbook(*NORMAL); await run_for(1)
    t_start = CLK[0]; await run_for(2.5, lambda s: RICH)
    check(S.pos is not None, "持续 ≥2s 且 ≥3 个样本后开仓")
    p = S.pos
    if p:
        check(abs(CLK[0] - t_start - 2250) <= 250 or CLK[0] - t_start >= 2000, f"开仓发生在信号出现后 ≥2s（{(p['t'] - t_start) / 1000:.2f}s）")
        check(abs(p["pxIo"] - 1778.4) < 1e-6 and abs(p["pxLt"] - 1766.4) < 1e-6, f"入场价取实际成交 io {p['pxIo']} / Lighter {p['pxLt']}")
        check(abs(p["entrySpread"] - (1778.4 / 1766.4 - 1) * 1e4) < 1e-6, f"实际入场价差 {p['entrySpread']:+.2f}bp 写入 pos")
        check(p["feesIn"] > 0 and not p["weak"], f"入场手续费 {p['feesIn']:.5f}U，weak={p['weak']}")
        REV = (1772.5, 1772.9, 1766.9, 1767.3)        # 可成交出场价差 1772.9/1766.9 = +34.0bp ≤ 中位数
        await run_for(10, lambda s: REV)
        check(S.pos is not None, f"10s 时已回归（出场价差 {bot.LIVE['exitSpr']:+.1f}bp），但最短持仓 30s 未到 → 不平｜{bot.LIVE.get('holdNote')}")
        await run_for(21, lambda s: REV)
        check(S.pos is None and len(S.trades) == 1, "持仓 ≥30s 且回归、利润够 → 平仓")
        if S.trades:
            r = S.trades[-1]; q = p["qIo"]
            exp = (1778.4 - 1772.9) * q + (1766.9 - 1766.4) * q - q * (1778.4 + 1772.9) * 0.009 / 100 - q * (1766.4 + 1766.9) * 0.028 / 100
            check(abs(r["u"] - exp) < 1e-9, f"实际收益 {r['u']:+.5f}U = 手算 {exp:+.5f}U（含两边手续费）")
            check(abs(r["uBal"] - exp) < 1e-6, f"余额变化 {r['uBal']:+.5f}U 一致")
            check(r["real"] and r["uEst"] is not None, f"盘口估算另存 uEst={r['uEst']:+.5f}U，reason={r['reason']}")
    # ---------- D ----------
    print("D 下单前复核：设杠杆/查持仓期间价差消失 → 放弃")
    reset(); setbook(*NORMAL); await run_for(1)
    orig = V.balances
    async def bal_and_move():
        setbook(*NORMAL); return await orig()
    V.balances = bal_and_move
    await run_for(2.5, lambda s: RICH)
    V.balances = orig
    check(S.pos is None and not S.trades and any("下单前复核" in e["msg"] for e in S.olog[-5:]), "复核失败，不下单")
    check(bot.DRY_CASH["io"] == 100.0, "没有任何模拟成交")
    # ---------- E ----------
    print("E 止损：开仓 3 秒后价差继续拉大 ≥100bp → 立即止损（不受最短持仓限制）")
    reset(); setbook(*NORMAL); await run_for(1); await run_for(2.5, lambda s: RICH)
    check(S.pos is not None, "开仓")
    await run_for(3, lambda s: (1796.0, 1796.4, 1766.0, 1766.4))
    check(S.pos is None and S.trades and S.trades[-1]["reason"] == "止损", f"止损平仓 {S.trades[-1]['u']:+.4f}U" if S.trades else "止损平仓")
    # ---------- F ----------
    print("F io 成交滑点 25bp（入场偏离偏小）：回归到中位数但扣费后 <3bp → 继续持有；之后有利润再平")
    reset(); setbook(*NORMAL); await run_for(1)
    bot.DRY_SLIP["io"] = 25.0
    await run_for(2.5, lambda s: RICH); bot.DRY_SLIP["io"] = 0.0
    p = S.pos
    check(p is not None and p["weak"], f"实际入场偏离 {p['entryDev']:+.1f}bp < 12.5bp → weak" if p else "开仓")
    if p:
        R1 = (1772.6, 1773.0, 1766.6, 1767.0)        # 出场价差 1773.0/1766.6=+36.2bp：已回归，但收益不够
        await run_for(40, lambda s: R1)
        check(S.pos is not None, f"40s：已回归但收益 {bot.LIVE['posBp']:+.1f}bp <3bp → 不平｜{bot.LIVE.get('holdNote')}")
        R2 = (1771.4, 1771.8, 1767.0, 1767.4)        # 出场价差 1771.8/1767.0=+27.2bp
        await run_for(1, lambda s: R2)
        check(S.pos is None and S.trades and S.trades[-1]["u"] > 0, f"收益够了才平 {S.trades[-1]['u']:+.4f}U ({S.trades[-1]['bp']:+.1f}bp)" if S.trades else "平仓")
    # ---------- G ----------
    print("G 强平距离保护：开仓 2 秒后价格整体 +27% → 立即两边平")
    reset(); setbook(*NORMAL); await run_for(1); await run_for(2.5, lambda s: RICH)
    k = 1.27
    await run_for(2, lambda s: tuple(x * k for x in RICH))
    check(S.pos is None and S.trades and S.trades[-1]["reason"].startswith("强平距离保护"), S.trades[-1]["reason"] if S.trades else "强平保护")
    # ---------- H ----------
    print("H 一键全平")
    reset(); setbook(*NORMAL); await run_for(1); await run_for(2.5, lambda s: RICH)
    await bot.kill_all()
    check(S.pos is None and S.mode == "stopped" and S.trades and S.trades[-1]["reason"] == "一键全平", "全平并停止，记录实际收益")
    check(abs(bot.DRY_CASH["io"] + bot.DRY_CASH["lt"] - 100.0 - 100.0 - S.trades[-1]["u"]) < 1e-9, "余额变化 = 记录收益")
    # ---------- snapshot ----------
    snap = bot.public_snapshot(); json.dumps(snap)
    check(all(k in snap["cfg"] for k in ("confirmSec", "minProfitBp", "minHoldSec")), "看板快照带新参数")
    print("\n全部通过" if not FAIL else f"\n失败 {len(FAIL)} 项: {FAIL}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
