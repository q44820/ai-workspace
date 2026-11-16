#!/usr/bin/env python3
"""
机票查询 v2 - Google Flights 抓取
重庆/成都 → 上海，3月15-28日
"""

import json
import re
import time
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

ROUTES = []  # 历史行程已移除；新入口使用显式机场参数。
START_DATE = "2026-03-15"
END_DATE = "2026-03-28"


def search_google_flights(page, dep_code, arr_code, date_str):
    """Google Flights 查询"""
    # Google Flights URL 格式
    url = (
        f"https://www.google.com/travel/flights/search"
        f"?tfs=CBwQAhooEgoyMDI2LTAzLTE4agwIAhIIL20vMHFjNnlyDAoCEggvbS8wNndreg"
        # 直接用搜索页面
    )
    # 更简单的方式: 直接用 Google Flights 的搜索 URL
    url = f"https://www.google.com/travel/flights?q=flights+from+{dep_code}+to+{arr_code}+on+{date_str}&curr=CNY"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(5000)

        content = page.content()
        # 抓取价格
        prices = re.findall(r'(?:CN)?[¥￥]\s*([\d,]+)', content)
        prices += re.findall(r'(\d{3,4})\s*元', content)

        if prices:
            prices = [int(p.replace(',', '')) for p in prices if 50 < int(p.replace(',', '')) < 5000]
            if prices:
                return {"price": min(prices), "source": "google"}

        return None
    except Exception as e:
        return {"error": str(e)}


def search_booking(page, dep_code, arr_code, date_str):
    """Booking.com flights"""
    url = (
        f"https://flights.booking.com/flights/{dep_code}-{arr_code}/"
        f"?type=ONEWAY&adults=1&cabinClass=ECONOMY"
        f"&from={dep_code}&to={arr_code}&depart={date_str}"
    )
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(6000)

        content = page.content()
        text = page.inner_text("body")

        # 抓取价格 (CNY/RMB)
        prices = re.findall(r'(?:CNY|RMB|CN¥|¥|￥)\s*([\d,]+)', text)
        prices += re.findall(r'([\d,]+)\s*(?:CNY|RMB|元)', text)

        if prices:
            prices = [int(p.replace(',', '')) for p in prices if 50 < int(p.replace(',', '')) < 5000]
            if prices:
                return {"price": min(prices), "source": "booking"}

        return None
    except Exception as e:
        return {"error": str(e)}


def search_trip_com(page, dep_code, arr_code, date_str):
    """Trip.com (携程国际版，反爬较弱)"""
    url = (
        f"https://www.trip.com/flights/{dep_code.lower()}-to-{arr_code.lower()}/tickets-"
        f"{dep_code.lower()}{arr_code.lower()}?dcity={dep_code}&acity={arr_code}"
        f"&ddate={date_str}&rdate=&flighttype=oneway&class=y&lowprice=&quantity=1"
    )
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(6000)

        content = page.content()
        text = page.inner_text("body")

        # 抓取价格
        prices = re.findall(r'(?:CNY|RMB|CN¥|¥|￥)\s*([\d,]+)', text)
        if not prices:
            prices = re.findall(r'(?:CNY|RMB|CN¥|¥|￥)\s*([\d,]+)', content)

        if prices:
            prices = [int(p.replace(',', '')) for p in prices if 50 < int(p.replace(',', '')) < 5000]
            if prices:
                return {"price": min(prices), "source": "trip.com"}

        return None
    except Exception as e:
        return {"error": str(e)}


def main():
    print("=" * 60)
    print("✈️  机票查询 v2 - Google/Booking/Trip.com")
    print(f"📅 日期范围: {START_DATE} ~ {END_DATE}")
    print("🚫 已过滤凌晨航班")
    print("=" * 60)

    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
        # 注入反检测
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => false});
        """)

        page = context.new_page()

        for route in ROUTES:
            route_key = f"{route['dep_name']}→{route['arr_name']}"
            results[route_key] = {}
            print(f"\n🔍 查询 {route_key} ...")

            current = datetime.strptime(START_DATE, "%Y-%m-%d")
            end = datetime.strptime(END_DATE, "%Y-%m-%d")

            while current <= end:
                date_str = current.strftime("%Y-%m-%d")
                weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][current.weekday()]

                print(f"  📅 {date_str} ({weekday}) ... ", end="", flush=True)

                result = None

                # 依次尝试 Trip.com → Booking → Google
                for searcher, name in [
                    (search_trip_com, "trip.com"),
                    (search_booking, "booking"),
                    (search_google_flights, "google"),
                ]:
                    try:
                        r = searcher(page, route["dep"], route["arr"], date_str)
                        if r and "price" in r:
                            result = r
                            break
                    except Exception:
                        continue

                if result and "price" in result:
                    results[route_key][date_str] = {
                        "weekday": weekday,
                        "cheapest": result["price"],
                        "source": result.get("source", "?"),
                    }
                    print(f"¥{result['price']} (via {result.get('source', '?')})")
                else:
                    results[route_key][date_str] = {"weekday": weekday, "error": "无结果"}
                    print("❌ 无结果")

                current += timedelta(days=1)
                time.sleep(2)

        browser.close()

    # ─── 汇总 ───
    print("\n" + "=" * 60)
    print("📊 查询结果汇总")
    print("=" * 60)

    for route_key, dates in results.items():
        print(f"\n✈️ {route_key}")
        print(f"{'日期':<14} {'星期':<6} {'最低价':<8} {'来源'}")
        print("-" * 45)

        valid = {d: v for d, v in dates.items() if "cheapest" in v}
        for d in sorted(dates):
            info = dates[d]
            if "cheapest" in info:
                print(f"{d:<14} {info['weekday']:<6} ¥{info['cheapest']:<7} {info.get('source', '?')}")
            else:
                print(f"{d:<14} {info['weekday']:<6} {'查询失败'}")

        if valid:
            best = min(valid, key=lambda x: valid[x]["cheapest"])
            b = valid[best]
            print(f"\n🏆 最便宜: {best} ({b['weekday']}) ¥{b['cheapest']}")

    out = "./runtime/flight_results.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n💾 已保存: {out}")


if __name__ == '__main__':
    raise SystemExit('历史入口停用：请使用 ../query.py 的显式参数入口；勿运行过期日期任务。')
