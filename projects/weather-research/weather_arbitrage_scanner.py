#!/usr/bin/env python3
"""
天气套利扫描器 v1.0
基于 @yummyuyu_ + @zywunkg0319 方法论

核心逻辑：
1. 延迟套利 - METAR实时数据 vs WU/Polymarket 3-10分钟延迟
2. 防守+彩票仓位 - 高概率No + 小概率Yes
3. 城市优先级 - Paris > Ankara > 其他，避免Seoul

Usage: python3 weather_arbitrage_scanner.py
"""

import requests
import json
import datetime
import time

# ============ 配置 ============
CITIES_PRIORITY = {
    'LFPG': {'name': 'Paris', 'priority': 1, 'timezone': 1},      # 内陆，升温潜力大
    'LTAC': {'name': 'Ankara', 'priority': 2, 'timezone': 3},     # 波动大，机会多
    'EGLL': {'name': 'London', 'priority': 3, 'timezone': 0},     # 海洋性，难预测
    'KORD': {'name': 'Chicago', 'priority': 4, 'timezone': -6},   # 参考
    'KLGA': {'name': 'NYC', 'priority': 5, 'timezone': -5},       # 参考
}

AVOID_CITIES = ['RKSI']  # Seoul - 数据源不透明

# 方法论参数
MIN_EDGE_PERCENT = 5           # 5% edge才下单
DEFENSE_NO_THRESHOLD = 0.80    # 防守仓位No价 > 80%
LOTTERY_YES_THRESHOLD = 0.10   # 彩票仓位Yes价 < 10%
MIN_LIQUIDITY = 3000           # 最小流动性$3K
MAX_SETTLEMENT_HOURS = 48      # 最大48小时结算

class WeatherArbitrageScanner:
    def __init__(self):
        self.session = requests.Session()
        self.results = []
        
    def get_metar_data(self, airport_code):
        """获取METAR实时数据"""
        try:
            url = f"https://aviationweather.gov/api/data/metar?ids={airport_code}&format=json"
            r = self.session.get(url, timeout=10)
            data = r.json()
            if data:
                return data[0]
        except Exception as e:
            print(f"METAR获取失败 {airport_code}: {e}")
        return None
    
    def analyze_weather(self, metar):
        """基于方法论分析气象条件"""
        if not metar:
            return None
            
        temp_c = metar.get('temp')
        temp_f = temp_c * 9/5 + 32 if temp_c else None
        dewp_c = metar.get('dewp')
        wind = metar.get('wspd')
        vis = metar.get('visib')
        clouds = metar.get('clouds', [])
        
        # 云层分析
        cloud_cover = clouds[0].get('cover', 'N/A') if clouds else 'N/A'
        cloud_base = clouds[0].get('base', 0) if clouds else 0
        
        analysis = {
            'temp_c': temp_c,
            'temp_f': temp_f,
            'dewp_c': dewp_c,
            'wind': wind,
            'vis': vis,
            'cloud_cover': cloud_cover,
            'cloud_base': cloud_base,
            'conditions': []
        }
        
        # 方法论判断
        if cloud_cover in ['CLR', 'FEW']:
            analysis['conditions'].append('✅ 云量少，升温有利')
            analysis['warming_potential'] = 'high'
        elif cloud_cover == 'SCT':
            analysis['conditions'].append('⚠️ 云量适中，升温一般')
            analysis['warming_potential'] = 'medium'
        elif cloud_cover in ['BKN', 'OVC']:
            analysis['conditions'].append('⚠️ 云层厚(BKN/OVC)，升温受限')
            analysis['warming_potential'] = 'low'
        else:
            analysis['warming_potential'] = 'unknown'
        
        # 温度判断
        if temp_c and temp_c < 5:
            analysis['conditions'].append('❄️ 低温，关注逆温层/积雪')
        elif temp_c and temp_c > 15:
            analysis['conditions'].append('🌡️ 较高温度，接近上限')
            
        # 湿度判断 (露点差)
        if temp_c and dewp_c:
            dewpoint_depression = temp_c - dewp_c
            if dewpoint_depression < 3:
                analysis['conditions'].append('💧 高湿度(湿冷)，蒸发耗热')
        
        return analysis
    
    def get_polymarket_weather(self):
        """获取Polymarket天气市场"""
        markets = []
        try:
            r = self.session.get(
                'https://gamma-api.polymarket.com/markets',
                params={'active': 'true', 'closed': 'false', 'limit': 100},
                timeout=15
            )
            data = r.json()
            for m in data:
                q = m.get('question', '').lower()
                if 'temperature' in q or 'high temp' in q:
                    markets.append(m)
        except Exception as e:
            print(f"Polymarket API错误: {e}")
        return markets
    
    def generate_trading_plan(self, city_code, city_info, metar, analysis):
        """生成交易计划"""
        plan = {
            'city': city_info['name'],
            'priority': city_info['priority'],
            'current_temp_c': analysis['temp_c'],
            'current_temp_f': analysis['temp_f'],
            'warming_potential': analysis['warming_potential'],
            'conditions': analysis['conditions'],
            'recommendations': []
        }
        
        temp_c = analysis['temp_c']
        
        # 根据当前温度推荐区间
        if temp_c:
            # 防守推荐 - 比当前温度低2-3度的区间No
            defense_temp = int(temp_c) - 3
            if defense_temp > 0:
                plan['recommendations'].append({
                    'type': '防守',
                    'action': f'买入 {defense_temp}°C No',
                    'logic': f'当前{temp_c}°C，{defense_temp}°C达成概率低',
                    'target_price': '> $0.85'
                })
            
            # 彩票推荐 - 比当前温度高3-5度的区间Yes
            lottery_temp = int(temp_c) + 4
            plan['recommendations'].append({
                'type': '彩票',
                'action': f'买入 {lottery_temp}°C Yes',
                'logic': f'若暖平流发力突破云层，可能达{lottery_temp}°C',
                'target_price': '< $0.10 (5X+潜力)'
            })
        
        return plan
    
    def scan(self):
        """主扫描流程"""
        print("=" * 70)
        print("🌤️ 天气套利扫描器 v1.0")
        print("基于 @yummyuyu_ + @zywunkg0319 方法论")
        print("=" * 70)
        print()
        print(f"扫描时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()
        
        all_plans = []
        
        for code, info in sorted(CITIES_PRIORITY.items(), key=lambda x: x[1]['priority']):
            print(f"\n【{info['name']}】({code})")
            print("-" * 50)
            
            # 获取METAR
            metar = self.get_metar_data(code)
            if not metar:
                print("  ❌ 无法获取METAR数据")
                continue
            
            # 分析气象
            analysis = self.analyze_weather(metar)
            if not analysis:
                print("  ❌ 分析失败")
                continue
            
            print(f"  温度: {analysis['temp_c']}°C ({analysis['temp_f']:.1f}°F)")
            print(f"  云量: {analysis['cloud_cover']}")
            print(f"  风速: {analysis['wind']}kt")
            print(f"  能见度: {analysis['vis']}sm")
            
            print()
            for cond in analysis['conditions']:
                print(f"  {cond}")
            
            # 生成交易计划
            plan = self.generate_trading_plan(code, info, metar, analysis)
            all_plans.append(plan)
            
            print()
            print("  📋 交易计划:")
            for rec in plan['recommendations']:
                print(f"    [{rec['type']}] {rec['action']}")
                print(f"      逻辑: {rec['logic']}")
                print(f"      目标价: {rec['target_price']}")
        
        # 汇总
        print()
        print("=" * 70)
        print("📊 今日交易计划汇总")
        print("=" * 70)
        
        defense_trades = []
        lottery_trades = []
        
        for plan in sorted(all_plans, key=lambda x: x['priority']):
            for rec in plan['recommendations']:
                trade = {
                    'city': plan['city'],
                    'action': rec['action'],
                    'logic': rec['logic']
                }
                if rec['type'] == '防守':
                    defense_trades.append(trade)
                else:
                    lottery_trades.append(trade)
        
        print("\n🛡️ 防守仓位 (高概率No):")
        for t in defense_trades[:3]:
            print(f"  - {t['city']}: {t['action']}")
        
        print("\n🎰 彩票仓位 (小概率Yes):")
        for t in lottery_trades[:3]:
            print(f"  - {t['city']}: {t['action']}")
        
        print()
        print("⚠️  风控提醒:")
        print("  - 仅参与结算时间 > 6小时的市场")
        print("  - 避开Seoul (数据源不透明)")
        print("  - 沿海城市谨慎参与")
        print("  - 实时盯METAR，有变化立即调整")
        
        return all_plans

if __name__ == "__main__":
    raise SystemExit('历史硬编码天气样本，不是实时观测；禁止直接当策略输出。先替换官方实时数据源并验收。')
