#!/usr/bin/env python3
"""机票查询 v2 - 去哪儿网"""
import re, json, sys, time
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

ROUTES = []  # 由显式参数提供，不保留历史行程。
START = "2026-03-15"
END = "2026-03-27"

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) "
      "Version/17.0 Mobile/15E148 Safari/604.1")

WEEKDAYS = ["周一","周二","周三","周四","周五","周六","周日"]

def get_body_text(page):
    """多种方式获取 body 文本"""
    try:
        el = page.query_selector("body")
        if el:
            return el.inner_text()
    except:
        pass
    try:
        return page.content()
    except:
        return ""

def search_date(page, dep, arr, date_str):
    """查询单日航班"""
    url = (f"https://m.flight.qunar.com/ncs/page/flightlist?"
           f"depCity={dep}&arrCity={arr}&goDate={date_str}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(5000)

        body = get_body_text(page)

        # 提取价格
        prices = [int(x) for x in re.findall(r'[¥￥](\d+)', body) if 80 < int(x) < 5000]

        # 尝试解析航班详情
        flights = []
        # 匹配模式: 航司 时间-时间 价格
        blocks = re.findall(
            r'([\u4e00-\u9fa5]+航空|春秋航空|吉祥航空|华夏航空|西部航空|海南航空|四川航空|山东航空|厦门航空|深圳航空|中国国航|东方航空|南方航空|联合航空)'
            r'.*?(\d{2}:\d{2}).*?(\d{2}:\d{2}).*?[¥￥](\d+)',
            body
        )
        for airline, dep_t, arr_t, price in blocks:
            p = int(price)
            if p < 80 or p > 5000:
                continue
            h = int(dep_t.split(":")[0])
            if h < 7:  # 过滤凌晨
                continue
            flights.append({
                "price": p,
                "airline": airline,
                "dep_time": dep_t,
                "arr_time": arr_t,
            })

        if not flights and prices:
            flights = [{"price": min(prices), "airline": "?", "dep_time": "?", "arr_time": "?"}]

        return flights
    except Exception as e:
        return [{"error": str(e)[:100]}]

def main():
    print("=" * 60)
    print("✈️  机票查询 v2 - 重庆/成都 → 上海")
    print(f"📅 {START} ~ {END} | 🚫 已过滤凌晨航班")
    print("=" * 60)

    all_results = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(
            user_agent=UA,
            viewport={"width": 375, "height": 812},
            locale="zh-CN",
        )
        page = ctx.new_page()

        for route in ROUTES:
            key = f"{route['dep']}→{route['arr']}"
            all_results[key] = {}
            print(f"\n🔍 {key}")

            cur = datetime.strptime(START, "%Y-%m-%d")
            end = datetime.strptime(END, "%Y-%m-%d")

            while cur <= end:
                ds = cur.strftime("%Y-%m-%d")
                wd = WEEKDAYS[cur.weekday()]
                sys.stdout.write(f"  {ds} ({wd}) ... ")
                sys.stdout.flush()

                flights = search_date(page, route["dep"], route["arr"], ds)

                if flights and "error" not in flights[0]:
                    flights.sort(key=lambda x: x.get("price", 99999))
                    best = flights[0]
                    all_results[key][ds] = {
                        "weekday": wd,
                        "price": best["price"],
                        "airline": best["airline"],
                        "dep_time": best["dep_time"],
                        "arr_time": best["arr_time"],
                        "count": len(flights),
                    }
                    print(f"¥{best['price']} | {best['airline']} {best['dep_time']}-{best['arr_time']} | {len(flights)}班")
                else:
                    err = flights[0].get("error","?") if flights else "无"
                    all_results[key][ds] = {"weekday": wd, "error": err}
                    print(f"❌ {err[:60]}")

                cur += timedelta(days=1)
                time.sleep(1.5)

            # 截个图留证
            page.screenshot(path=f"./runtime/flight_{route['dep']}.png")

        browser.close()

    # 汇总
    print("\n" + "=" * 60)
    print("📊 汇总")
    print("=" * 60)

    for key, dates in all_results.items():
        print(f"\n✈️ {key}")
        print(f"  {'日期':<12} {'星期':<5} {'价格':<7} {'航司':<8} {'出发':<6} {'到达'}")
        print("  " + "-" * 50)

        valid = {}
        for ds in sorted(dates):
            info = dates[ds]
            if "price" in info:
                valid[ds] = info
                print(f"  {ds:<12} {info['weekday']:<5} ¥{info['price']:<6} {info['airline']:<8} {info['dep_time']:<6} {info['arr_time']}")
            else:
                print(f"  {ds:<12} {info['weekday']:<5} {'查询失败'}")

        if valid:
            best_d = min(valid, key=lambda d: valid[d]["price"])
            b = valid[best_d]
            print(f"\n  🏆 最便宜: {best_d} ({b['weekday']}) ¥{b['price']} {b['airline']} {b['dep_time']}")

    # 保存
    with open("./runtime/flight_results.json", "w") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n💾 已保存 flight_results.json")

if __name__ == '__main__':
    raise SystemExit('历史入口停用：请使用 ../query.py 的显式参数入口；勿运行过期日期任务。')
