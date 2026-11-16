#!/usr/bin/env python3
"""
携程机票价格自动查询脚本
使用 Actionbook Extension 控制 Chrome 查询 4 条航线 13 天的价格
"""

import subprocess
import json
import time
import re
from datetime import datetime, timedelta

# 航线配置
ROUTES = []  # 历史行程已移除，需显式提供参数。

# 日期范围：3月15日到3月27日
START_DATE = datetime(2026, 3, 15)
DAYS = 13

def get_prices_from_page():
    """从当前页面提取价格日历和航班列表"""
    js = """
    (function() {
        const results = {calendar: {}, flights: []};
        
        // 提取价格日历（顶部日期栏）
        const dateEls = document.querySelectorAll('[class*="date"], [class*="calendar"], .date-item, [data-date]');
        for (const el of dateEls) {
            const text = el.innerText || '';
            const match = text.match(/(\d{2}-\d{2}).*?¥(\d+)/);
            if (match) {
                results.calendar[match[1]] = parseInt(match[2]);
            }
        }
        
        // 提取航班列表
        const flightCards = document.querySelectorAll('[class*="flight-item"], [class*="list-item"], .flight');
        for (const card of flightCards.slice(0, 10)) {
            const airlineEl = card.querySelector('[class*="airline"], .airline, img[alt]');
            const timeEls = card.querySelectorAll('[class*="time"], .time');
            const priceEl = card.querySelector('[class*="price"], .price');
            
            const airline = airlineEl?.innerText?.trim() || airlineEl?.alt || '';
            const priceText = priceEl?.innerText?.match(/¥(\d+)/);
            const price = priceText ? parseInt(priceText[1]) : 0;
            
            let departTime = '';
            for (const t of timeEls) {
                const txt = t.innerText?.trim();
                if (txt && txt.match(/^\d{2}:\d{2}$/)) {
                    departTime = txt;
                    break;
                }
            }
            
            if (price > 0 && airline) {
                results.flights.push({airline, price, departTime});
            }
        }
        
        return JSON.stringify(results);
    })()
    """
    
    try:
        r = subprocess.run(
            ['actionbook', '--browser-mode', 'extension', 'browser', 'eval', js],
            capture_output=True, text=True, timeout=20
        )
        return json.loads(r.stdout.strip().strip('"').replace('\\"', '"'))
    except Exception as e:
        return {"error": str(e), "calendar": {}, "flights": []}

def query_flight(route, date):
    """查询单条航线单日价格"""
    date_str = date.strftime("%Y-%m-%d")
    url = f"https://flights.ctrip.com/online/list/oneway-{route['from']}-{route['to']}?depdate={date_str}&cabin=Y&adult=1"
    
    # 打开页面
    subprocess.run(
        ['actionbook', '--browser-mode', 'extension', 'browser', 'goto', url],
        capture_output=True, timeout=20
    )
    time.sleep(4)  # 等待加载
    
    # 提取数据
    data = get_prices_from_page()
    
    # 找出最低价（排除凌晨00:00-06:00）
    best_flight = None
    for f in data.get('flights', []):
        if not f.get('departTime'):
            continue
        hour = int(f['departTime'].split(':')[0])
        if hour < 6:  # 排除凌晨
            continue
        if not best_flight or f['price'] < best_flight['price']:
            best_flight = f
    
    return {
        "date": date.strftime("%-m/%-d"),
        "price": best_flight['price'] if best_flight else 0,
        "airline": best_flight.get('airline', '') if best_flight else '',
        "time": best_flight.get('departTime', '') if best_flight else ''
    }

def main():
    print("开始查询机票价格...")
    print("="*80)
    
    results = {}
    for route in ROUTES:
        print(f"\n查询航线: {route['name']}")
        results[route['name']] = []
        
        for i in range(DAYS):
            date = START_DATE + timedelta(days=i)
            result = query_flight(route, date)
            results[route['name']].append(result)
            print(f"  {result['date']}: ¥{result['price']} {result['airline']} {result['time']}")
            time.sleep(2)
    
    # 输出表格
    print("\n" + "="*80)
    print("查询完成！生成表格...")
    
    # 保存结果
    with open('/tmp/flight_prices.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"结果已保存到 /tmp/flight_prices.json")
    return results

if __name__ == '__main__':
    raise SystemExit('历史入口停用：请使用 ../query.py 的显式参数入口；勿运行过期日期任务。')
