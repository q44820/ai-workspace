#!/usr/bin/env python3
"""
天气模拟账户结算脚本
- 检查已到期的模拟仓位
- 从 Polymarket 抓取结算结果
- 更新 sim_log.json 记录盈亏
- 推送 Telegram 报告
"""
import json, subprocess, sys, datetime
from pathlib import Path

LOG_PATH = Path(__file__).parent / "sim_log.json"

def get_market_result(url: str, outcome: str) -> dict:
    """用 Playwright 抓取市场结算结果"""
    script = f"""
from playwright.sync_api import sync_playwright
import json

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()
    page.goto("{url}")
    page.wait_for_timeout(5000)
    text = page.evaluate("() => document.body.innerText")
    browser.close()
    print(text[:3000])
"""
    result = subprocess.run(["python3", "-c", script], capture_output=True, text=True, timeout=30)
    raw = result.stdout

    # 判断是否结算
    settled = "Resolved" in raw or "resolved" in raw or "Winner" in raw
    # 检查目标outcome是否赢了
    won = outcome in raw and ("Yes" in raw or "Resolved" in raw)

    return {
        "raw_snippet": raw[:500],
        "settled": settled,
        "won": won
    }

def settle_positions():
    log = json.loads(LOG_PATH.read_text())
    today = datetime.date.today().isoformat()
    total_pnl = 0
    settled_count = 0
    report_lines = [f"🌤️ 天气模拟账户结算报告 — {today}\n"]

    for pos in log["positions"]:
        if pos["status"] != "open":
            continue
        if pos["settle_date"] > today:
            continue

        # 尝试结算
        print(f"结算: {pos['outcome']} @ {pos['market']}")
        result = get_market_result(pos["url"], pos["outcome"].split()[0])

        if not result["settled"]:
            report_lines.append(f"⏳ {pos['outcome']} ({pos['market'][:20]}) — 未结算")
            continue

        # 计算盈亏
        if result["won"]:
            pnl = pos["potential_payout"] - pos["size_usd"]
            pos["result"] = "WIN"
            emoji = "✅"
        else:
            pnl = -pos["size_usd"]
            pos["result"] = "LOSS"
            emoji = "❌"

        pos["pnl"] = round(pnl, 2)
        pos["status"] = "settled"
        total_pnl += pnl
        settled_count += 1

        report_lines.append(
            f"{emoji} {pos['type']} | {pos['outcome']} | {pos['market'][:25]}\n"
            f"   投入${pos['size_usd']} → {'盈利' if pnl>0 else '亏损'}${abs(pnl):.2f}"
        )

    # 更新账户余额
    log["account"]["current_balance"] = round(log["account"]["current_balance"] + total_pnl, 2)
    log["account"]["total_pnl"] = round(log["account"].get("total_pnl", 0) + total_pnl, 2)

    # 记录今日汇总
    if today not in log["daily_summary"]:
        log["daily_summary"][today] = {}
    log["daily_summary"][today]["settled_pnl"] = round(total_pnl, 2)
    log["daily_summary"][today]["settled_count"] = settled_count

    # 写回
    LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2))

    # 生成报告
    open_count = sum(1 for p in log["positions"] if p["status"] == "open")
    report_lines.append(f"\n📊 今日结算: {settled_count}单 | PnL: {'+'if total_pnl>=0 else ''}{total_pnl:.2f}")
    report_lines.append(f"💰 模拟账户余额: ${log['account']['current_balance']}")
    report_lines.append(f"📂 未结算仓位: {open_count}单")
    report_lines.append(f"📈 累计PnL: ${log['account']['total_pnl']:+.2f}")

    report = "\n".join(report_lines)
    print(report)
    return report

if __name__ == '__main__':
    raise SystemExit('旧版文字匹配结算不可靠：禁止据此写入模拟账，待替换官方结果核验。')
