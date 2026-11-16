#!/usr/bin/env python3
"""
Polymarket v5 "Buy No" 智能扫描
核心策略：找"大概率不会按时发生"的事件，利用乐观偏差买 No

专注5个有真实确定性edge的类型（血泪精简版）：
1. Pre-Market FDV/TGE - 项目延期 + 项目方操纵
2. 产品发布 deadline - 科技产品几乎总延期
3. 极端/猎奇事件 - 概率被猎奇推高，天然No edge
4. 天气延迟套利 - METAR数据可验证，机械性edge
5. 临期高确定性 - 结算前3-7天，结果基本板上钉钉

❌ 已踢掉（血泪教训）：
- 政客承诺/政策 → Iran/Israel系列亏损惨烈
- 立法/法案 → 信息劣势，结果难预判
- IPO/上市 → 流动性差，市场数量少
"""

import json
import os
import sys
from datetime import datetime, timezone

import requests

GAMMA_API = "https://gamma-api.polymarket.com/markets"
REPORT_PATH = os.path.join(os.path.dirname(__file__), 'runtime', 'scan_report.md')

# 市场分类规则：关键词 → 类型 + 延期概率 + edge 评分
# 只保留有真实、可验证 edge 的5类
CATEGORIES = [
    {
        "name": "Pre-Market FDV/TGE",
        "icon": "🪙",
        "delay_prob": 90,
        "edge": 5,
        "keywords": ["fdv", "tge", "token launch", "pre-market", "premarket", "mainnet launch",
                     "one day after launch", "day after launch", "trading above", "valued above",
                     "market cap"],
    },
    {
        "name": "产品发布/科技 Deadline",
        "icon": "💻",
        "delay_prob": 85,
        "edge": 4,
        "keywords": ["release", "released", "ship", "shipped", "deploy", "deployed",
                     "launched by", "available by", "beta by", "gpt", "gemini",
                     "grok", "claude", "llama", "sora", "gta vi", "gta 6"],
    },
    {
        "name": "极端/猎奇事件",
        "icon": "👽",
        "delay_prob": 99,
        "edge": 5,
        "keywords": ["alien", "ufo", "jesus", "extraterrestrial", "world war",
                     "nuclear war", "asteroid", "extinction", "rapture", "end of world",
                     "zombie", "second coming"],
    },
    {
        "name": "天气延迟套利",
        "icon": "🌤️",
        "delay_prob": 80,
        "edge": 5,
        "keywords": ["temperature in", "high temp", "highest temperature",
                     "low temp", "lowest temperature", "weather in",
                     "paris", "ankara", "london", "chicago", "madrid"],
    },
    {
        "name": "临期高确定性",
        "icon": "⏰",
        "delay_prob": 85,
        "edge": 5,
        "keywords": [],  # 不靠关键词，靠 days_left + no_price 组合判断（见下方scoring）
    },
]

# 排除的市场（无结构性偏差）
EXCLUDE_KEYWORDS = ["nba", "nfl", "mlb", "nhl ", "soccer", "football match",
                    "premier league", "champions league", "serie a", "la liga",
                    "bundesliga", "ligue 1", "cricket", "tennis", "golf",
                    "boxing", "ufc", "mma", "formula 1", "f1 ", "nascar",
                    "wrestling", "olympics medal", "gold medal", "medal count"]


def fetch_markets(limit=500):
    r = requests.get(
        GAMMA_API,
        params={"active": "true", "closed": "false", "limit": limit,
                "order": "volume24hr", "ascending": "false"},
        timeout=20,
    )
    return json.loads(r.text)


def classify_market(market, yes_price=0, no_price=0, days_left=999):
    """判断市场属于哪个类型，返回分类信息或 None"""
    q = market.get("question", "").lower()
    desc = (market.get("description", "") or "")[:500].lower()
    text = q + " " + desc

    # 排除体育
    for ex in EXCLUDE_KEYWORDS:
        if ex in q:
            return None

    # 匹配关键词类别（前4类）
    for cat in CATEGORIES:
        if not cat["keywords"]:
            continue  # 跳过临期类（单独处理）
        for kw in cat["keywords"]:
            if kw in text:
                return cat

    # 临期高确定性：不靠关键词，靠 no_price + days_left 组合判断
    # 排除体育/政治/已知差类别后，高No价+即将到期 = 有确定性的市场
    if days_left <= 14 and no_price >= 0.70 and yes_price >= 0.25:
        return next(c for c in CATEGORIES if c["name"] == "临期高确定性")

    return None


def parse_prices(market):
    """解析 Yes/No 价格"""
    raw = market.get("outcomePrices", "")
    try:
        if isinstance(raw, str):
            prices = json.loads(raw)
        else:
            prices = raw
        yes_p = float(prices[0]) if prices else 0
        no_p = float(prices[1]) if len(prices) > 1 else 0
        return yes_p, no_p
    except Exception:
        return 0, 0


def analyze_no_undervalue(market, cat, no_price, days_left=999):
    """评估 No 被低估的程度（0-100）"""
    score = 0
    volume24h = float(market.get("volume24hr", 0) or 0)
    cat_name = cat["name"]

    # 基础分：延期概率 vs No 价格隐含概率
    implied_no_prob = no_price  # No 价格 = 市场认为不发生的概率
    delay_prob = cat["delay_prob"] / 100

    if delay_prob > implied_no_prob:
        # No 被低估：真实不发生概率 > 市场价格
        gap = delay_prob - implied_no_prob
        score += min(gap * 100, 50)  # 最高 50 分

    # 流动性低 = 更容易被操纵 = No 更可能被低估
    if volume24h < 10000:
        score += 15
    elif volume24h < 50000:
        score += 10
    elif volume24h < 100000:
        score += 5

    # edge 评分加成
    score += cat["edge"] * 3

    # No 价格在甜蜜区间（0.15-0.50）= 利润空间大
    if 0.15 <= no_price <= 0.50:
        score += 15
    elif 0.10 <= no_price < 0.15:
        score += 10
    elif 0.50 < no_price <= 0.65:
        score += 5

    # ── 天气类专项加分（METAR可验证，机械性edge）──
    if cat_name == "天气延迟套利":
        score += 10  # 天气类天然加分：数据可验证
        if no_price >= 0.80:
            score += 10  # 防守仓位甜蜜区（@yummyuyu_ 风格：高概率No）
        if volume24h >= 3000:
            score += 5   # 流动性足够才能进出

    # ── 临期高确定性专项加分（rich_adul 临期理财逻辑）──
    if cat_name == "临期高确定性":
        if days_left <= 3 and no_price >= 0.82:
            score += 30  # 3天内+高No价 = 最强确定性
        elif days_left <= 7 and no_price >= 0.75:
            score += 20  # 7天内+较高No价
        elif days_left <= 14 and no_price >= 0.70:
            score += 10  # 两周内也算

    # ── 资金锁定惩罚（days_left过长降优先级）──
    if days_left > 60:
        score -= 10  # 锁太久，机会成本高
    elif days_left > 30:
        score -= 5

    return min(score, 100)


def days_until(end_date_str):
    """计算剩余天数"""
    if not end_date_str:
        return 999
    try:
        end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return max(0, (end - now).days)
    except Exception:
        return 999


def scan():
    markets = fetch_markets(500)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    results = []

    for m in markets:
        yes_p, no_p = parse_prices(m)
        if no_p <= 0.05 or no_p >= 0.92:
            continue  # 太极端或太贵（天气类No常在0.85-0.95，放宽上限）
        # V神/NeverYes 铁律：Yes必须≥25%（市场真的"疯狂"才值得买No）
        if yes_p < 0.25:
            continue

        volume24h = float(m.get("volume24hr", 0) or 0)
        volume = float(m.get("volume", 0) or 0)
        question = m.get("question", "?")
        end_date = m.get("endDate", "")
        days_left = days_until(end_date)
        cond_id = m.get("conditionId", "") or m.get("condition_id", "")
        clob_ids = m.get("clobTokenIds", "")

        # 传入价格和剩余天数辅助分类
        cat = classify_market(m, yes_p, no_p, days_left)
        if not cat:
            continue

        undervalue_score = analyze_no_undervalue(m, cat, no_p, days_left)

        results.append({
            "question": question,
            "category": cat["name"],
            "icon": cat["icon"],
            "delay_prob": cat["delay_prob"],
            "edge": cat["edge"],
            "yes_price": yes_p,
            "no_price": no_p,
            "volume24h": volume24h,
            "volume": volume,
            "days_left": days_left,
            "end_date": end_date[:10] if end_date else "?",
            "undervalue_score": undervalue_score,
            "condition_id": cond_id,
            "clob_ids": clob_ids,
        })

    # 按低估分数排序
    results.sort(key=lambda x: x["undervalue_score"], reverse=True)

    # ────────────────────────────────────────────────
    # 🔒 关联性去重：同一主体/事件只保留最高分的一个
    # 防止买互斥市场（如 Elon 90-114 Yes + 115-139 No）
    # ────────────────────────────────────────────────
    SUBJECT_KEYWORDS = [
        "elon musk", "elon", "trump", "biden", "israel", "iran",
        "russia", "ukraine", "china", "fed", "powell", "bitcoin",
        "ethereum", "nvidia", "apple", "meta", "google", "amazon",
        "tesla", "spacex", "doge", "solana", "bnb", "xrp",
    ]

    def extract_subject(question):
        """提取市场主体关键词"""
        q = question.lower()
        for kw in SUBJECT_KEYWORDS:
            if kw in q:
                return kw
        # 没有匹配到关键词，用前20个字符作为 key（避免完全不去重）
        return q[:20]

    seen_subjects = {}  # subject -> best_score
    deduped = []
    for r in results:
        subj = extract_subject(r["question"])
        if subj not in seen_subjects:
            seen_subjects[subj] = r["undervalue_score"]
            deduped.append(r)
        else:
            # 同一主体已有更高分的，跳过；否则替换
            if r["undervalue_score"] > seen_subjects[subj]:
                # 移除旧的，加入新的
                deduped = [x for x in deduped if extract_subject(x["question"]) != subj]
                seen_subjects[subj] = r["undervalue_score"]
                deduped.append(r)
            # else: 分数不如旧的，跳过

    removed = len(results) - len(deduped)
    if removed > 0:
        print(f"  ⚠️ 关联去重：移除 {removed} 个重复主体市场")
    results = deduped

    # 生成报告
    report = []
    report.append(f"# Polymarket v5 'Buy No' 智能扫描")
    report.append(f"")
    report.append(f"**扫描时间**: {now_str} HKT")
    report.append(f"**扫描范围**: {len(markets)} 个市场")
    report.append(f"**筛选出有 edge 的市场**: {len(results)} 个")
    report.append(f"")

    # 按分类分组
    by_cat = {}
    for r in results:
        cat_name = f"{r['icon']} {r['category']}"
        if cat_name not in by_cat:
            by_cat[cat_name] = []
        by_cat[cat_name].append(r)

    for cat_name, items in by_cat.items():
        report.append(f"## {cat_name}")
        report.append(f"")
        for i, item in enumerate(items[:8], 1):
            q = item["question"][:65]
            score_bar = "🟢" if item["undervalue_score"] >= 50 else "🟡" if item["undervalue_score"] >= 30 else "⚪"
            report.append(f"{i}. **{q}**")
            report.append(f"   No: ${item['no_price']:.2f} | Yes: ${item['yes_price']:.2f} | "
                         f"24h量: ${item['volume24h']:,.0f} | 到期: {item['end_date']} ({item['days_left']}天)")
            report.append(f"   低估评分: {score_bar} {round(item['undervalue_score'], 1)}/100 | "
                         f"延期概率: {item['delay_prob']}%")
            report.append(f"")

    # 推荐交易
    top = [r for r in results if r["undervalue_score"] >= 30 and r["volume24h"] >= 1000][:10]
    if top:
        report.append(f"## 🎯 推荐交易 (低估评分 ≥ 30)")
        report.append(f"")
        report.append(f"| # | 市场 | No价格 | 低估分 | 到期 | 24h量 |")
        report.append(f"|---|------|--------|--------|------|-------|")
        for i, t in enumerate(top, 1):
            q = t["question"][:40]
            report.append(f"| {i} | {q} | ${t['no_price']:.2f} | {t['undervalue_score']} | "
                         f"{t['end_date']} | ${t['volume24h']:,.0f} |")
        report.append(f"")

    report_text = "\n".join(report)

    # 保存报告
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write(report_text)

    # 打印
    print(report_text)

    # 打印摘要给 cron 推送
    print(f"\n{'='*50}")
    print(f"总结: {len(results)} 个有 edge 的市场，{len(top)} 个推荐交易")
    if top:
        print(f"Top 3:")
        for t in top[:3]:
            print(f"  - {t['question'][:50]} | No ${t['no_price']:.2f} | 分数 {t['undervalue_score']}")


if __name__ == "__main__":
    scan()
