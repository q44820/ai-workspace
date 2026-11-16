#!/usr/bin/env python3
"""
Polymarket 市场扫描器 — 纯代码，零 token
输出候选市场 JSON 供 AI 选品

v2 更新：
- 加入本地 score_market() 评分（0-100），只把真正有 edge 的候选喂给 AI
- 加入 HARD_BLACKLIST 硬过滤（代码层，不烧 AI token）
- 只保留 score >= 25 的候选，按分数降序，最多输出 TOP 10 给 AI
"""

import json
import os
from datetime import datetime, timezone
import requests

GAMMA_API = "https://gamma-api.polymarket.com/markets"
CANDIDATES_PATH = os.path.join(os.path.dirname(__file__), 'runtime', 'candidates.json')

# 市场分类：关键词 → 类型 + 延期概率
CATEGORIES = [
    {
        "name": "Pre-Market FDV/TGE",
        "delay_prob": 90,
        "keywords": ["fdv", "tge", "token launch", "pre-market", "premarket", "mainnet launch",
                     "one day after launch", "day after launch", "trading above", "valued above",
                     "market cap"],
    },
    {
        "name": "产品发布/科技 Deadline",
        "delay_prob": 85,
        "keywords": ["release", "released", "ship", "shipped", "deploy", "deployed",
                     "launched by", "available by", "beta by", "gpt", "gemini",
                     "grok", "claude", "llama", "sora", "gta vi", "gta 6"],
    },
    {
        "name": "IPO/上市",
        "delay_prob": 80,
        "keywords": ["ipo", "go public", "public offering", "list on", "listed on",
                     "direct listing"],
    },
    {
        "name": "政客承诺/政策",
        "delay_prob": 75,
        "keywords": ["trump", "biden", "executive order", "sign into law", "announce",
                     "peace deal", "cease fire", "ceasefire", "nuclear deal", "trade deal",
                     "agreement", "resign", "fired", "impeach", "removed from office",
                     "pardon", "ban", "starmer", "macron"],
    },
    {
        "name": "立法/法案",
        "delay_prob": 80,
        "keywords": ["bill", "act pass", "passed by", "senate vote", "house vote",
                     "congress", "legislation", "ratif", "confirm"],
    },
    {
        "name": "极端/猎奇事件",
        "delay_prob": 99,
        "keywords": ["alien", "ufo", "jesus", "extraterrestrial", "world war",
                     "nuclear war", "asteroid", "extinction"],
    },
    # ✅ rich_adul 策略：谜题/挑战类 — 极难在短期完成，No 结构性优势
    {
        "name": "谜题/挑战/竞赛",
        "delay_prob": 92,
        "keywords": ["puzzle", "solved by", "challenge", "solve the", "decipher",
                     "crack the", "mystery", "treasure hunt", "million dollar",
                     "impossible", "guinness"],
    },
    # ✅ rich_adul 策略：法律起诉/逮捕类 — 法律程序慢，短期内起诉概率极低
    {
        "name": "法律/起诉/逮捕",
        "delay_prob": 88,
        "keywords": ["indicted", "indictment", "charged with", "arrested by",
                     "convicted", "sentenced", "plead guilty", "prosecution",
                     "warrant", "extradited"],
    },
    # ✅ rich_adul 策略：辞职/解雇/弹劾类 — 需极大政治压力
    {
        "name": "辞职/解雇/弹劾",
        "delay_prob": 85,
        "keywords": ["resign", "resignation", "fired by", "removed from office",
                     "impeached", "impeachment", "ousted", "step down",
                     "recalled", "no confidence"],
    },
    # ✅ 天气市场 — 用 METAR 数据判断
    {
        "name": "天气/温度",
        "delay_prob": 95,
        "keywords": ["temperature", "highest temperature", "weather", "celsius", "fahrenheit"],
    },
    # ✅ 电竞/LOL — 11chips_ 策略
    {
        "name": "电竞/LOL",
        "delay_prob": 95,
        "keywords": ["league of legends", "lol ", "dota", "csgo", "esports", "esports",
                     "game winner", "first blood", "handicap", "games total"],
    },
    # ✅ 体育 — NBA/NFL/MLB/冰球/格斗
    {
        "name": "体育/NBA-NFL",
        "delay_prob": 95,
        "keywords": ["nba", "nfl", "mlb", "hockey", "ufc", "mma", "boxing",
                     "soccer", "football", "premier league", "champions league",
                     "stanley cup", "world series", "super bowl"],
    },
]

# 只排除美股短期价格类市场（只能做市收租，不能猎手下注）
EXCLUDE_KEYWORDS = [
    "up or down on", "close above $", "close below $",
    "up or down this week", "up or down february", "up or down march",
    " spy above", " qqq above", " spx above", " ndx above",
]

# ─────────────────────────────────────────────
# 本地硬过滤（代码层，不需要 AI）— 血泪教训总结
# ─────────────────────────────────────────────
HARD_BLACKLIST = [
    # 美股价格类（血泪教训）
    "close above $", "close below $", "up or down on",
    "up or down this week", "up or down february", "up or down march",
    "spy above", "qqq above", "spx above", "ndx above",
    # 政治/战争类（血泪教训）
    "strike iran", "strikes iran", "invade", "nuclear war",
    "israel strikes", "russia invade",
]


def is_blacklisted(question: str) -> bool:
    """代码层黑名单过滤，命中直接排除，不消耗 AI token"""
    q = question.lower()
    return any(kw in q for kw in HARD_BLACKLIST)


def score_market(market: dict, cat: dict, yes_price: float, no_price: float, days_left: int) -> int:
    """
    本地评分，0-100，越高越值得 AI 判断。
    移植自 polymarket_monitor_v5.py undervalue_score 逻辑并增强。
    """
    score = 0
    volume24h = float(market.get("volume24hr", 0) or 0)

    # 1. 延期概率 vs No 价格 → gap 越大越值钱
    delay_prob = cat["delay_prob"] / 100
    gap = delay_prob - no_price
    if gap > 0:
        score += min(gap * 100, 50)

    # 2. 流动性（有流动性才能进出）
    if 3000 <= volume24h < 10000:
        score += 12
    elif volume24h >= 10000:
        score += 8   # 太热门反而 edge 被吃掉
    elif volume24h < 500:
        score -= 10  # 流动性太差

    # 3. edge 分类加成
    score += cat.get("edge", 3) * 3

    # 4. No 价甜蜜区（0.15-0.85）
    if 0.15 <= no_price <= 0.50:
        score += 15
    elif 0.50 < no_price <= 0.75:
        score += 8

    # 5. 天气类加分（METAR 可验证）
    if "天气" in cat["name"] or "温度" in cat["name"]:
        score += 10
        if no_price >= 0.80:
            score += 10  # 防守仓位

    # 6. 临期高确定性加分
    if days_left <= 7 and no_price >= 0.75:
        score += 20
    elif days_left <= 3 and no_price >= 0.82:
        score += 30

    # 7. 资金锁定惩罚
    if days_left > 60:
        score -= 10
    elif days_left > 30:
        score -= 5

    # 8. Yes 必须 >= 25%（市场有疯狂才有 edge）
    if yes_price < 0.25:
        score -= 30

    return max(0, min(score, 100))


def fetch_markets(limit=500):
    """用 polymarket CLI 获取市场数据，比 gamma-api 更全"""
    import subprocess
    
    all_markets = {}
    
    try:
        # 1. 用 list 获取常规市场
        result = subprocess.run(
            ["polymarket", "markets", "list", "--active", "true", "--limit", "300", "-o", "json"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            for m in data:
                all_markets[m.get('id')] = m
            print(f"list 获取 {len(data)} 个")
        
        # 2. 用 search 获取天气/电竞/体育市场
        for kw in ["temperature", "lol", "league of legends", "dota", "esports",
                   "nba", "nfl", "hockey", "ufc", "mma"]:
            result = subprocess.run(
                ["polymarket", "markets", "search", kw, "--limit", "50", "-o", "json"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                for m in data:
                    all_markets[m.get('id')] = m
                print(f"search '{kw}' 获取 {len(data)} 个")
        
        if all_markets:
            return list(all_markets.values())
            
    except Exception as e:
        print(f"CLI failed: {e}, falling back to gamma-api")
    
    # 回退到 gamma-api
    r = requests.get(GAMMA_API, params={
        "active": "true", "closed": "false", "limit": limit,
        "order": "volume24hr", "ascending": "false",
    }, timeout=20)
    return json.loads(r.text)


def classify(market):
    q = market.get("question", "").lower()
    desc = (market.get("description", "") or "")[:500].lower()
    text = q + " " + desc
    for ex in EXCLUDE_KEYWORDS:
        if ex in q:
            return None
    for cat in CATEGORIES:
        for kw in cat["keywords"]:
            if kw in text:
                return cat
    return None


def parse_prices(market):
    raw = market.get("outcomePrices", "")
    try:
        prices = json.loads(raw) if isinstance(raw, str) else raw
        return float(prices[0]), float(prices[1])
    except Exception:
        return 0, 0


def days_until(end_date_str):
    if not end_date_str:
        return 999
    try:
        end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        return max(0, (end - datetime.now(timezone.utc)).days)
    except Exception:
        return 999


def scan():
    markets = fetch_markets(500)
    candidates = []

    # 天气/电竞/体育关键词 - 这些市场是短期的，不受 days_left < 2 限制
    QUICK_KW = ["temperature", "weather", "celsius", "fahrenheit", 
                "lol", "league of legends", "dota", "game winner", "first blood",
                "nba", "nfl", "mlb", "hockey", "ufc", "mma", "boxing", "soccer"]

    filtered_out = 0  # 统计黑名单过滤数量

    for m in markets:
        q = (m.get("question", "") or "").lower()
        question = m.get("question", "?")

        # ── 本地硬过滤：黑名单命中直接跳过，不烧 AI token ──
        if is_blacklisted(question):
            filtered_out += 1
            continue

        # 检查是否是天气/电竞
        is_quick = any(kw in q for kw in QUICK_KW)

        cat = classify(m)
        if not cat:
            continue
        yes_p, no_p = parse_prices(m)

        # 天气/电竞允许 0.03-0.97 价格，其他类别保持 0.05-0.95
        if is_quick:
            if no_p <= 0.03 or no_p >= 0.97:
                continue
        else:
            if no_p <= 0.05 or no_p >= 0.85:
                continue

        volume24h = float(m.get("volume24hr", 0) or 0)
        if volume24h < 1000:
            continue

        # ❌ 铁律：普通市场当天和明天结算的绝对不进候选（days_left < 2）
        # 但天气/电竞是短期的，不受这个限制
        dl = days_until(m.get("endDate", ""))
        if not is_quick and dl < 2:
            continue

        end_date = m.get("endDate", "")
        cond_id = m.get("conditionId", "") or m.get("condition_id", "")
        clob_ids = m.get("clobTokenIds", "")

        # ── 本地评分：0-100 ──
        mkt_score = score_market(m, cat, yes_p, no_p, dl)

        # 只保留 score >= 25 的候选（排除明显没价值的）
        if mkt_score < 25:
            continue

        candidates.append({
            "question": question,
            "category": cat["name"],
            "delay_prob": cat["delay_prob"],
            "yes_price": round(yes_p, 4),
            "no_price": round(no_p, 4),
            "volume24h": round(volume24h, 0),
            "days_left": dl,
            "end_date": end_date[:10] if end_date else "?",
            "condition_id": cond_id,
            "clob_ids": clob_ids,
            # ── v2 新增字段 ──
            "score": mkt_score,
            "filter_reason": None,
            "ai_needed": True,
        })

    print(f"黑名单过滤: {filtered_out} 个市场")

    # 按本地评分降序排列，只喂 TOP 10 给 AI（减少 token 消耗）
    candidates.sort(key=lambda x: x["score"], reverse=True)

    # 只保留前 10 个最优候选，减少 AI 分析的 token 消耗
    candidates = candidates[:10]

    output = {
        "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_markets": len(markets),
        "candidates": len(candidates),
        "markets": candidates,
    }

    os.makedirs(os.path.dirname(CANDIDATES_PATH), exist_ok=True)
    with open(CANDIDATES_PATH, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"扫描完成: {len(markets)} 市场 → {len(candidates)} 个候选")
    return output


if __name__ == "__main__":
    scan()
