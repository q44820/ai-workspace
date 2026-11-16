#!/usr/bin/env python3
"""
机票查询脚本 - 用 Playwright 抓取携程低价日历
查询 重庆/成都 → 上海 3月15日-3月28日 的最低票价
"""

import json
import re
import sys
import time
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

# ─── 配置 ───
ROUTES = []  # 由显式参数提供；不保留个人历史行程。
# 日期范围
START_DATE = "2026-03-15"
END_DATE = "2026-03-28"

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)


def search_ctrip(page, dep_code, arr_code, date_str):
    """携程移动端查询单日航班"""
    url = (
        f"https://m.ctrip.com/html5/flight/swift/domestic/"
        f"{dep_code}/{arr_code}/{date_str}"
    )
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        # 等待航班列表加载
        page.wait_for_timeout(4000)

        # 尝试多种选择器抓取价格
        flights = []

        # 方法1: 抓取页面内容中的价格信息
        content = page.content()

        # 用正则匹配价格 (¥xxx 或 ￥xxx)
        prices = re.findall(r'[¥￥]\s*(\d+)', content)
        if prices:
            prices = [int(p) for p in prices if 50 < int(p) < 5000]

        # 方法2: 尝试抓取具体航班信息
        try:
            items = page.query_selector_all('[class*="flight-item"], [class*="list-item"], [class*="FlightItem"]')
            for item in items[:10]:
                text = item.inner_text()
                # 提取航司、时间、价格
                time_match = re.findall(r'(\d{2}:\d{2})', text)
                price_match = re.findall(r'[¥￥]\s*(\d+)', text)
                airline_match = re.findall(r'([\u4e00-\u9fa5]+航空|春秋|吉祥|华夏|西部|成都|厦门|奥凯|长龙)', text)

                if price_match:
                    flight = {
                        "price": int(price_match[0]),
                        "airline": airline_match[0] if airline_match else "未知",
                        "dep_time": time_match[0] if len(time_match) >= 1 else "?",
                        "arr_time": time_match[1] if len(time_match) >= 2 else "?",
                    }
                    # 过滤凌晨航班 (00:00-06:59)
                    if flight["dep_time"] != "?" :
                        hour = int(flight["dep_time"].split(":")[0])
                        if hour < 7:
                            continue
                    flights.append(flight)
        except Exception:
            pass

        # 如果方法2没抓到，用方法1的价格
        if not flights and prices:
            flights = [{"price": min(prices), "airline": "未知", "dep_time": "?", "arr_time": "?"}]

        return flights

    except Exception as e:
        return [{"error": str(e)}]


def search_qunar(page, dep_name, arr_name, date_str):
    """去哪儿移动端查询"""
    url = (
        f"https://m.flight.qunar.com/ncs/page/flightlist?"
        f"depCity={dep_name}&arrCity={arr_name}&goDate={date_str}"
        f"&backDate=&sort=&airLine=&from="
    )
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(4000)

        flights = []
        content = page.content()

        # 正则提取价格
        prices = re.findall(r'[¥￥]\s*(\d+)', content)
        if prices:
            prices = [int(p) for p in prices if 50 < int(p) < 5000]

        # 尝试抓取航班列表
        try:
            items = page.query_selector_all('[class*="flight"], [class*="item"], [class*="row"]')
            for item in items[:15]:
                text = item.inner_text()
                time_match = re.findall(r'(\d{2}:\d{2})', text)
                price_match = re.findall(r'[¥￥]\s*(\d+)', text)
                airline_match = re.findall(r'([\u4e00-\u9fa5]+航空|春秋|吉祥|华夏|西部|成都|厦门|奥凯|长龙)', text)

                if price_match:
                    p = int(price_match[0])
                    if p < 50 or p > 5000:
                        continue
                    flight = {
                        "price": p,
                        "airline": airline_match[0] if airline_match else "未知",
                        "dep_time": time_match[0] if len(time_match) >= 1 else "?",
                        "arr_time": time_match[1] if len(time_match) >= 2 else "?",
                    }
                    if flight["dep_time"] != "?":
                        hour = int(flight["dep_time"].split(":")[0])
                        if hour < 7:
                            continue
                    flights.append(flight)
        except Exception:
            pass

        if not flights and prices:
            flights = [{"price": min(prices), "airline": "未知", "dep_time": "?", "arr_time": "?"}]

        return flights

    except Exception as e:
        return [{"error": str(e)}]


def main():
    print("=" * 60)
    print("✈️  机票查询 - 重庆/成都 → 上海")
    print(f"📅 日期范围: {START_DATE} ~ {END_DATE}")
    print("🚫 已过滤凌晨航班 (00:00-06:59)")
    print("=" * 60)

    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            user_agent=MOBILE_UA,
            viewport={"width": 375, "height": 812},
            locale="zh-CN",
        )
        page = context.new_page()

        # 遍历每条航线
        for route in ROUTES:
            route_key = f"{route['dep_name']}→{route['arr_name']}"
            results[route_key] = {}
            print(f"\n🔍 查询 {route_key} ...")

            # 遍历日期
            current = datetime.strptime(START_DATE, "%Y-%m-%d")
            end = datetime.strptime(END_DATE, "%Y-%m-%d")

            while current <= end:
                date_str = current.strftime("%Y-%m-%d")
                weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][current.weekday()]

                print(f"  📅 {date_str} ({weekday}) ... ", end="", flush=True)

                # 先试携程
                flights = search_ctrip(page, route["dep"], route["arr"], date_str)

                # 携程没结果试去哪儿
                if not flights or (flights and "error" in flights[0]):
                    flights_q = search_qunar(page, route["dep_name"], route["arr_name"], date_str)
                    if flights_q and "error" not in flights_q[0]:
                        flights = flights_q

                if flights and "error" not in flights[0]:
                    # 按价格排序，取最便宜的
                    flights.sort(key=lambda x: x.get("price", 99999))
                    cheapest = flights[0]
                    results[route_key][date_str] = {
                        "weekday": weekday,
                        "cheapest": cheapest["price"],
                        "airline": cheapest.get("airline", "?"),
                        "dep_time": cheapest.get("dep_time", "?"),
                        "arr_time": cheapest.get("arr_time", "?"),
                        "total_flights": len(flights),
                    }
                    print(f"¥{cheapest['price']} ({cheapest.get('airline', '?')} {cheapest.get('dep_time', '?')})")
                else:
                    err = flights[0].get("error", "未知错误") if flights else "无结果"
                    results[route_key][date_str] = {"weekday": weekday, "error": err}
                    print(f"❌ {err[:50]}")

                current += timedelta(days=1)
                time.sleep(1)  # 礼貌间隔

        browser.close()

    # ─── 汇总输出 ───
    print("\n" + "=" * 60)
    print("📊 查询结果汇总")
    print("=" * 60)

    for route_key, dates in results.items():
        print(f"\n✈️ {route_key}")
        print(f"{'日期':<14} {'星期':<6} {'最低价':<8} {'航司':<10} {'时间'}")
        print("-" * 55)

        valid_dates = {d: v for d, v in dates.items() if "cheapest" in v}
        for date_str, info in sorted(dates.items()):
            if "cheapest" in info:
                print(f"{date_str:<14} {info['weekday']:<6} ¥{info['cheapest']:<7} {info['airline']:<10} {info['dep_time']}-{info['arr_time']}")
            else:
                print(f"{date_str:<14} {info['weekday']:<6} {'查询失败'}")

        if valid_dates:
            best_date = min(valid_dates, key=lambda d: valid_dates[d]["cheapest"])
            best = valid_dates[best_date]
            print(f"\n🏆 最便宜: {best_date} ({best['weekday']}) ¥{best['cheapest']} {best['airline']}")

    # 保存结果
    out_path = "./runtime/flight_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n💾 结果已保存: {out_path}")


if __name__ == '__main__':
    raise SystemExit('历史入口停用：请使用 ../query.py 的显式参数入口；勿运行过期日期任务。')
