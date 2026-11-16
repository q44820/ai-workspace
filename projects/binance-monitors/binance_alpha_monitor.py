#!/usr/bin/env python3
"""Binance Alpha 空投监控（来源 alpha123.uk）

功能：
- 拉取 https://alpha123.uk/api/data?fresh=1
- 对比上次状态，检测「新增空投」、「时间确认（time 从空变非空）」、「积分/数量变更」、「合约确认」
- 同一个空投最多只推 ALERT_CAP 次（默认 3 次），超过后只更新本地状态、不再推送
- 推送渠道：WxPusher（默认）/ Telegram（可选）/ 仅写日志（PUSH=0）
- 由 launchd 或 cron 定时调用

输出：
- 状态文件: runtime/binance_alpha_state.json
- 日志文件: runtime/binance_alpha_monitor.log
- 默认沉默；只有变化才推送，且每个空投上限 3 次

环境变量：
- ALPHA_MONITOR_PUSH=1            是否推送（默认 0，0 表示只记录日志）
- ALPHA_MONITOR_VERBOSE=1         调试输出
- ALPHA_MONITOR_CHANNEL=wxpusher  推送渠道，可选 wxpusher | telegram | both
- ALPHA_MONITOR_ALERT_CAP=3       同一空投最多推送几次，0 表示不限制
- WXPUSHER_APP_TOKEN              WxPusher 的 appToken（必填）
- WXPUSHER_UIDS                   WxPusher 的 UID 列表（逗号分隔，可省略，默认读 wxpusher_config.json）

配置文件 wxpusher_config.json（可选，避免环境变量）：
{
  "app_token": "",
  "uids": [""],
  "topic_ids": []
}
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
RUNTIME = Path(os.environ.get("BINANCE_MONITOR_RUNTIME", str(ROOT / "runtime"))).expanduser()
DISABLED_FLAG_PATH = RUNTIME / "binance_alpha_monitor.disabled"
if DISABLED_FLAG_PATH.exists():
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(RUNTIME / "binance_alpha_monitor.log", "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] DISABLED_FLAG_EXIT {DISABLED_FLAG_PATH}\n")
    except Exception:
        pass
    sys.exit(0)
STATE_PATH = RUNTIME / "binance_alpha_state.json"
LOG_PATH = RUNTIME / "binance_alpha_monitor.log"
WX_CONFIG_PATH = RUNTIME / "wxpusher_config.json"
API_URL = "https://alpha123.uk/api/data?fresh=1"
STABILITY_FEED_URL = "https://alpha123.uk/stability/stability_feed_v3.json"
WXPUSHER_API = "https://wxpusher.zjiecode.com/api/send/message"

PUSH = os.getenv("ALPHA_MONITOR_PUSH", "0") == "1"
VERBOSE = os.getenv("ALPHA_MONITOR_VERBOSE", "0") == "1"
CHANNEL = os.getenv("ALPHA_MONITOR_CHANNEL", "wxpusher").strip().lower()
try:
    ALERT_CAP = int(os.getenv("ALPHA_MONITOR_ALERT_CAP", "3"))
except ValueError:
    ALERT_CAP = 3
# 提前提醒窗口（秒），默认 60 分钟
try:
    IMMINENT_WINDOW_SEC = int(os.getenv("ALPHA_MONITOR_IMMINENT_SEC", "3600"))
except ValueError:
    IMMINENT_WINDOW_SEC = 3600
# 允许在开始后多少秒内再补一次（防 cron 卡顿）
try:
    IMMINENT_GRACE_SEC = int(os.getenv("ALPHA_MONITOR_IMMINENT_GRACE", "600"))
except ValueError:
    IMMINENT_GRACE_SEC = 600
# Alpha123 稳定度看板：状态绿色且价差基点 spr 低于阈值时提醒刷交易
STABILITY_MONITOR_ENABLED = os.getenv("ALPHA_STABILITY_MONITOR", "1") == "1"
try:
    STABILITY_SPREAD_THRESHOLD = float(os.getenv("ALPHA_STABILITY_SPREAD_THRESHOLD", "0.5"))
except ValueError:
    STABILITY_SPREAD_THRESHOLD = 0.5
try:
    STABILITY_ALERT_COOLDOWN_SEC = int(os.getenv("ALPHA_STABILITY_ALERT_COOLDOWN_SEC", "1800"))
except ValueError:
    STABILITY_ALERT_COOLDOWN_SEC = 1800

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://alpha123.uk/zh/",
    "Origin": "https://alpha123.uk",
    "sec-fetch-site": "same-origin",
    "sec-fetch-mode": "cors",
    "sec-fetch-dest": "empty",
}


# ---------- utils ----------
def log(msg: str) -> None:
    from telegram_runtime import redact_log
    msg = redact_log(msg)
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass
    if VERBOSE:
        print(line.rstrip(), file=sys.stderr)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"airdrops": {}, "alerts": {}, "stability_alerts": {}, "last_run_ts": 0}
    try:
        st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"airdrops": {}, "alerts": {}, "stability_alerts": {}, "last_run_ts": 0}
    st.setdefault("airdrops", {})
    st.setdefault("alerts", {})
    st.setdefault("stability_alerts", {})
    return st


def save_state(state: dict[str, Any]) -> None:
    state["last_run_ts"] = int(time.time())
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def fetch_api() -> dict[str, Any]:
    req = urllib.request.Request(API_URL, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)
    except Exception as first_exc:
        # Global env may point at a broken proxy/DNS path. Retry once direct-mode.
        log(f"fetch_api 代理/默认网络失败，尝试直连: {first_exc}")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)


def airdrop_key(item: dict[str, Any]) -> str:
    """Stable id, 优先用合约地址；否则用 token+date。"""
    addr = (item.get("contract_address") or "").lower()
    if addr:
        return f"addr:{addr}"
    token = (item.get("token") or "").upper()
    date = item.get("date") or ""
    return f"token:{token}:{date}"


def fmt_when(item: dict[str, Any]) -> str:
    d = item.get("date") or "?"
    t = item.get("time") or "—"
    utc = item.get("utc")
    if utc and utc != t:
        return f"{d} {t} (UTC {utc})"
    return f"{d} {t}"


def parse_decimal(value: Any) -> Decimal | None:
    if value in (None, "", "—"):
        return None
    s = str(value).strip().replace(",", "")
    if not s:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def fmt_compact_number(value: Any) -> str:
    n = parse_decimal(value)
    if n is None:
        return "—"
    if n >= Decimal("100000000"):
        v = (n / Decimal("100000000")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return f"{v.normalize()}亿"
    if n >= Decimal("10000"):
        v = (n / Decimal("10000")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        return f"{v.normalize()}万"
    if n == n.to_integral():
        return f"{int(n):,}"
    return str(n.normalize())


def share_count_value(item: dict[str, Any]) -> Decimal | None:
    """计算可领取份数：总量 total_amount / 单份 amount。"""
    amount = parse_decimal(item.get("amount"))
    total = parse_decimal(item.get("total_amount"))
    if amount is None or total is None or amount <= 0:
        return None
    return total / amount


def fmt_share_count(item: dict[str, Any]) -> str:
    shares = share_count_value(item)
    if shares is None:
        return "—"
    return f"约{fmt_compact_number(shares)}份"


def _http_json(url: str, timeout: int = 8) -> dict[str, Any] | list[Any] | None:
    req = urllib.request.Request(url, headers={"User-Agent": HEADERS["User-Agent"], "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status >= 400:
                return None
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        log(f"market_check 请求失败 {url[:80]}: {exc}")
        return None


def check_binance_symbol(token: str) -> dict[str, Any]:
    """Check whether TOKEN already trades on Binance spot/futures."""
    token = (token or "").upper().strip()
    if not token:
        return {"spot": False, "futures": False}
    symbol = urllib.parse.quote(f"{token}USDT")
    spot = _http_json(f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}")
    futures = _http_json(f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={symbol}")
    return {
        "spot": isinstance(spot, dict) and spot.get("symbol") == f"{token}USDT",
        "futures": isinstance(futures, dict) and futures.get("symbol") == f"{token}USDT",
    }


def _fmt_age(ms: int | float | None) -> tuple[str, float | None]:
    if not ms:
        return "未知", None
    try:
        age_days = max(0.0, (time.time() - float(ms) / 1000.0) / 86400.0)
    except Exception:
        return "未知", None
    if age_days >= 365:
        return f"约{age_days/365:.1f}年", age_days
    if age_days >= 30:
        return f"约{age_days/30:.1f}个月", age_days
    if age_days >= 1:
        return f"约{age_days:.1f}天", age_days
    return f"约{age_days*24:.1f}小时", age_days


def check_dexscreener(item: dict[str, Any]) -> dict[str, Any]:
    addr = (item.get("contract_address") or "").strip()
    if not addr:
        return {"has_pairs": False, "reason": "no_contract"}
    url = f"https://api.dexscreener.com/latest/dex/tokens/{urllib.parse.quote(addr)}"
    data = _http_json(url, timeout=10)
    pairs = []
    if isinstance(data, dict):
        pairs = data.get("pairs") or []
    if not isinstance(pairs, list) or not pairs:
        return {"has_pairs": False, "reason": "no_pairs"}
    # Sort by creation time; keep the oldest liquid-looking pair and aggregate rough stats.
    valid = [p for p in pairs if isinstance(p, dict)]
    oldest = min((p.get("pairCreatedAt") for p in valid if p.get("pairCreatedAt")), default=None)
    age_text, age_days = _fmt_age(oldest)
    liq_usd = 0.0
    vol24 = 0.0
    best_pair = None
    for pair in valid:
        try:
            liq_usd = max(liq_usd, float((pair.get("liquidity") or {}).get("usd") or 0))
        except Exception:
            pass
        try:
            vol24 = max(vol24, float((pair.get("volume") or {}).get("h24") or 0))
        except Exception:
            pass
        if best_pair is None:
            best_pair = pair
        else:
            try:
                if float((pair.get("liquidity") or {}).get("usd") or 0) > float((best_pair.get("liquidity") or {}).get("usd") or 0):
                    best_pair = pair
            except Exception:
                pass
    return {
        "has_pairs": True,
        "pair_count": len(valid),
        "oldest_age_text": age_text,
        "oldest_age_days": age_days,
        "max_liquidity_usd": round(liq_usd, 2),
        "max_volume24h_usd": round(vol24, 2),
        "dex": (best_pair or {}).get("dexId"),
        "chain": (best_pair or {}).get("chainId"),
        "url": (best_pair or {}).get("url"),
    }


def market_circulation_check(item: dict[str, Any]) -> dict[str, Any]:
    """Classify Alpha token as already-circulating vs likely fresh.

    This is an advisory label, not a trading signal. It checks:
    1) Binance spot/futures listing by TOKENUSDT
    2) DexScreener pairs by contract address, including pair age/liquidity/volume
    """
    token = str(item.get("token") or item.get("name") or "").upper().strip()
    binance = check_binance_symbol(token)
    dex = check_dexscreener(item)
    reasons: list[str] = []
    old = False
    if binance.get("spot"):
        old = True
        reasons.append("Binance现货已交易")
    if binance.get("futures"):
        old = True
        reasons.append("Binance合约已交易")
    if dex.get("has_pairs"):
        age_days = dex.get("oldest_age_days")
        if age_days is not None and age_days >= 7:
            old = True
            reasons.append(f"链上池子已存在{dex.get('oldest_age_text')}")
        elif age_days is not None and age_days >= 1:
            reasons.append(f"链上池子已存在{dex.get('oldest_age_text')}，疑似非首发")
        else:
            reasons.append(f"链上池子较新/{dex.get('oldest_age_text')}")
        if float(dex.get("max_liquidity_usd") or 0) >= 50000 or float(dex.get("max_volume24h_usd") or 0) >= 50000:
            old = True
            reasons.append("已有明显链上流动性/成交量")
    if old:
        label = "⚠️ 老币/已流通"
    elif dex.get("has_pairs"):
        label = "🟡 有链上池子，需谨慎"
    else:
        label = "🟢 未发现明显流通痕迹"
        reasons.append("未查到 Binance 交易或 DexScreener 池子")
    return {"label": label, "reasons": reasons[:4], "binance": binance, "dex": dex, "checked_at": int(time.time())}


def market_check_lines(item: dict[str, Any], html: bool = False) -> list[str]:
    mc = item.get("market_check") or {}
    if not isinstance(mc, dict) or not mc:
        return []
    lines = [f"🧪 市场流通: {mc.get('label') or '未知'}"]
    for reason in mc.get("reasons") or []:
        lines.append(f"  - {reason}")
    dex = mc.get("dex") or {}
    if isinstance(dex, dict) and dex.get("has_pairs"):
        lines.append(f"  - DEX: {dex.get('chain') or '?'} / {dex.get('dex') or '?'}，池子数 {dex.get('pair_count') or '?'}")
        lines.append(f"  - 流动性 ${fmt_compact_number(dex.get('max_liquidity_usd'))}，24h量 ${fmt_compact_number(dex.get('max_volume24h_usd'))}")
        if dex.get("url"):
            if html:
                lines.append(f"  - <a href=\"{dex.get('url')}\">DexScreener</a>")
            else:
                lines.append(f"  - DexScreener: {dex.get('url')}")
    return lines


def fmt_item_html(item: dict[str, Any]) -> str:
    head = f"<b>{item.get('token') or '?'}</b> ({item.get('name') or '?'})"
    rows = [
        f"🕐 时间: {fmt_when(item)}",
        f"⭐ 积分: {item.get('points') or '—'}",
        f"📦 单份数量: {item.get('amount') or '—'}",
        f"🎟️ 份数: {fmt_share_count(item)}",
        f"🔁 阶段: phase {item.get('phase') or '—'}",
        f"📌 状态: {item.get('status') or '—'}",
    ]
    addr = item.get("contract_address")
    if addr:
        chain = item.get("chain_id") or ""
        rows.append(f"🔗 合约: <code>{addr}</code> (chain {chain})")
    rows.extend(market_check_lines(item, html=True))
    return head + "<br>" + "<br>".join(rows)


def fmt_item_text(item: dict[str, Any]) -> str:
    head = f"{item.get('token') or '?'} ({item.get('name') or '?'})"
    rows = [
        f"🕐 时间: {fmt_when(item)}",
        f"⭐ 积分: {item.get('points') or '—'}",
        f"📦 单份数量: {item.get('amount') or '—'}",
        f"🎟️ 份数: {fmt_share_count(item)}",
        f"🔁 阶段: phase {item.get('phase') or '—'}",
        f"📌 状态: {item.get('status') or '—'}",
    ]
    addr = item.get("contract_address")
    if addr:
        chain = item.get("chain_id") or ""
        rows.append(f"🔗 合约: {addr} (chain {chain})")
    rows.extend(market_check_lines(item, html=False))
    return head + "\n" + "\n".join(rows)


def parse_scheduled_dt(item: dict[str, Any]) -> datetime | None:
    """把 (date, utc) 或 (date, time) 解析成 UTC 时区的 datetime。

    优先级：
    1) `utc` 字段（HH:MM）+ date：直接当 UTC
    2) `time` 字段（HH:MM）+ date：当 Asia/Shanghai (UTC+8)，再转 UTC
    解析失败返回 None。
    """
    date_str = (item.get("date") or "").strip()
    utc_str = (item.get("utc") or "").strip()
    time_str = (item.get("time") or "").strip()
    if not date_str:
        return None
    try:
        if utc_str and ":" in utc_str:
            dt = datetime.strptime(f"{date_str} {utc_str}", "%Y-%m-%d %H:%M")
            return dt.replace(tzinfo=timezone.utc)
        if time_str and ":" in time_str:
            cn_tz = timezone(timedelta(hours=8))
            dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            return dt.replace(tzinfo=cn_tz).astimezone(timezone.utc)
    except Exception as exc:
        log(f"parse_scheduled_dt 失败 {item.get('token')}: {exc}")
    return None


def diff_item(prev: dict[str, Any], curr: dict[str, Any]) -> list[str]:
    changes: list[str] = []
    fields = [
        ("status", "状态"),
        ("date", "日期"),
        ("time", "时间"),
        ("utc", "UTC时间"),
        ("points", "积分门槛"),
        ("amount", "单份数量"),
        ("total_amount", "总空投数量"),
        ("phase", "阶段"),
        ("futures_listed", "合约上市"),
        ("contract_address", "合约地址"),
        ("chain_id", "链ID"),
    ]
    for key, label in fields:
        a = prev.get(key)
        b = curr.get(key)
        if a == b:
            continue
        a_s = "—" if a in (None, "", False, "false") else str(a)
        b_s = "—" if b in (None, "", False, "false") else str(b)
        if a_s == b_s:
            continue
        changes.append(f"{label}: {a_s} → {b_s}")

    prev_shares = fmt_share_count(prev)
    curr_shares = fmt_share_count(curr)
    if prev_shares != curr_shares:
        changes.append(f"份数: {prev_shares} → {curr_shares}")
    return changes


# ---------- WxPusher ----------
def load_wxpusher_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {"app_token": "", "uids": [], "topic_ids": []}
    if WX_CONFIG_PATH.exists():
        try:
            data = json.loads(WX_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update(data)
        except Exception as exc:
            log(f"解析 wxpusher_config.json 失败: {exc}")

    env_token = os.getenv("WXPUSHER_APP_TOKEN")
    if env_token:
        cfg["app_token"] = env_token
    env_uids = os.getenv("WXPUSHER_UIDS")
    if env_uids:
        cfg["uids"] = [u.strip() for u in env_uids.split(",") if u.strip()]
    return cfg


def wxpusher_send(title: str, html_body: str) -> bool:
    cfg = load_wxpusher_config()
    token = (cfg.get("app_token") or "").strip()
    uids = [u for u in (cfg.get("uids") or []) if u]
    topic_ids = cfg.get("topic_ids") or []
    if not token:
        log("WxPusher appToken 未配置，跳过推送（请填 wxpusher_config.json）")
        return False
    if not uids and not topic_ids:
        log("WxPusher uids/topic_ids 都为空，跳过推送")
        return False

    payload = {
        "appToken": token,
        "summary": title[:20],
        "content": f"<h3>{title}</h3>{html_body}",
        "contentType": 2,  # 2 = HTML
        "uids": uids,
        "topicIds": topic_ids,
        "url": "https://alpha123.uk/zh/",
        "verifyPay": False,
    }
    req = urllib.request.Request(
        WXPUSHER_API,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(body)
        except Exception:
            data = {"raw": body}
        ok = bool(data.get("success")) or data.get("code") == 1000
        if not ok:
            log(f"WxPusher 返回失败: {body[:300]}")
        return bool(ok)
    except Exception as exc:
        log(f"WxPusher 请求异常: {exc}")
        return False


# ---------- Telegram (备用) ----------
def telegram_send(text: str) -> bool:
    sys.path.insert(0, str(ROOT))
    try:
        from telegram_runtime import load_telegram_runtime  # type: ignore
    except Exception as exc:
        log(f"无法导入 telegram_runtime: {exc}")
        return False
    bot, chat = load_telegram_runtime()
    if not bot or not chat:
        log("telegram bot 或 chat 未配置，跳过推送")
        return False
    url = f"https://api.telegram.org/bot{bot}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": chat,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = resp.status == 200
            if not ok:
                log(f"telegram 返回非200: {resp.status}")
            return ok
    except Exception as exc:
        log(f"telegram 推送失败: {exc}")
        return False


# ---------- 推送分发 ----------
def dispatch(title: str, html_body: str, text_body: str) -> bool:
    """根据 CHANNEL 环境变量决定走哪条通道。"""
    if not PUSH:
        log(f"PUSH=0，跳过推送：{title}")
        return False
    ok_any = False
    if CHANNEL in ("wxpusher", "both"):
        ok_any = wxpusher_send(title, html_body) or ok_any
    if CHANNEL in ("telegram", "both"):
        ok_any = telegram_send(f"<b>{title}</b>\n\n" + text_body) or ok_any
    if CHANNEL not in ("wxpusher", "telegram", "both"):
        log(f"未知 CHANNEL={CHANNEL}, 不推送")
    return ok_any



def today_hkt_date() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


def mark_stability_trading_done_today(state: dict[str, Any]) -> None:
    state["stability_trading_done_date"] = today_hkt_date()


# ---------- Alpha123 稳定度监控 ----------
def fetch_stability_feed() -> dict[str, Any]:
    req = urllib.request.Request(
        STABILITY_FEED_URL,
        headers={
            "User-Agent": HEADERS["User-Agent"],
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://alpha123.uk/zh/stability/",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as first_exc:
        log(f"stability_feed 默认网络失败，尝试直连: {first_exc}")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))


def stability_base_token(item: dict[str, Any]) -> str:
    name = str(item.get("n") or "").upper().strip()
    return name.split("/", 1)[0] if name else ""


def stability_status_label(st: str) -> str:
    color = (st or "").split(":", 1)[0]
    if color == "green":
        return "稳定"
    if color == "yellow":
        return "一般"
    if color == "red":
        return "不稳"
    return st or "未知"


def monitor_stability(state: dict[str, Any]) -> int:
    """Notify once when Alpha123 stability turns green and spread basis spr < threshold.

    页面默认逻辑也是看 statusColor=green + spr 小于阈值。这里把阈值固定成用户要的 0.5，
    持续稳定时冷却到期仍会再提醒；未达标会清除稳定标记。
    """
    if not STABILITY_MONITOR_ENABLED:
        return 0
    done_date = str(state.get("stability_trading_done_date") or "")
    if done_date == today_hkt_date():
        log(f"STABILITY_SKIP_DONE_TODAY {done_date}: 用户已标记今日交易完成，跳过稳定度提醒")
        return 0
    try:
        feed = fetch_stability_feed()
    except Exception as exc:
        log(f"stability_feed 获取失败: {exc}")
        return 0
    items = feed.get("items") if isinstance(feed, dict) else None
    if not isinstance(items, list):
        log("stability_feed items 字段异常")
        return 0

    now = int(time.time())
    recs: dict[str, Any] = state.get("stability_alerts") or {}
    pushed = 0
    seen_bases: set[str] = set()

    for item in items:
        if not isinstance(item, dict):
            continue
        base = stability_base_token(item)
        if not base or base == "KOGE":
            continue
        seen_bases.add(base)
        st = str(item.get("st") or "red:no_data")
        status_color = st.split(":", 1)[0]
        spr_raw = item.get("spr")
        try:
            spr = float(spr_raw)
        except (TypeError, ValueError):
            spr = None
        tight_stable = status_color == "green" and spr is not None and spr < STABILITY_SPREAD_THRESHOLD
        rec = recs.get(base) or {"notified_stable": False, "last_alert_ts": 0}

        if not tight_stable:
            # 变回一般/不稳/价差变宽后重置，下次重新稳定再提醒。
            if rec.get("notified_stable"):
                log(f"STABILITY_RESET {base}: st={st} spr={spr_raw}")
            rec["notified_stable"] = False
            rec["last_seen_ts"] = now
            rec["last_status"] = st
            rec["last_spr"] = spr_raw
            recs[base] = rec
            continue

        last_alert_ts = int(rec.get("last_alert_ts") or 0)
        if rec.get("notified_stable") and now - last_alert_ts < STABILITY_ALERT_COOLDOWN_SEC:
            rec["last_seen_ts"] = now
            rec["last_status"] = st
            rec["last_spr"] = spr_raw
            recs[base] = rec
            continue

        title = f"🟢 Alpha 稳定度达标：{base}"
        html = (
            f"<b>{base}</b> 当前稳定度达标，可以考虑去刷交易。<br>"
            f"状态: {stability_status_label(st)} / <code>{st}</code><br>"
            f"价差基点: <b>{spr:.4g}</b> &lt; {STABILITY_SPREAD_THRESHOLD}<br>"
            f"价格: {item.get('p') if item.get('p') is not None else '—'}<br>"
            f"4倍天数: {item.get('md') if item.get('md') is not None else '—'}<br>"
            f"<a href=\"https://alpha123.uk/zh/stability/\">打开稳定度看板</a>"
        )
        text = (
            f"{base} 当前稳定度达标，可以考虑去刷交易。\n"
            f"状态: {stability_status_label(st)} / {st}\n"
            f"价差基点: {spr:.4g} < {STABILITY_SPREAD_THRESHOLD}\n"
            f"价格: {item.get('p') if item.get('p') is not None else '—'}\n"
            f"4倍天数: {item.get('md') if item.get('md') is not None else '—'}\n"
            f"https://alpha123.uk/zh/stability/"
        )
        if dispatch(title, html, text):
            pushed += 1
            rec["notified_stable"] = True
            rec["last_alert_ts"] = now
            rec["last_alert_spr"] = spr
            log(f"STABILITY_PUSHED {base}: st={st} spr={spr}")
            time.sleep(0.5)
        else:
            log(f"STABILITY_PUSH_FAIL {base}: st={st} spr={spr}")
        rec["last_seen_ts"] = now
        rec["last_status"] = st
        rec["last_spr"] = spr_raw
        recs[base] = rec

    # 只保留 feed 中仍存在或最近 7 天内出现过的记录。
    cutoff = now - 7 * 86400
    state["stability_alerts"] = {
        k: v for k, v in recs.items()
        if k in seen_bases or int((v or {}).get("last_seen_ts") or 0) >= cutoff
    }
    return pushed


# ---------- 主流程 ----------
def main() -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    try:
        data = fetch_api()
    except Exception as exc:
        log(f"fetch_api 失败: {exc}")
        return 1

    airdrops = data.get("airdrops") or []
    if not isinstance(airdrops, list):
        log("airdrops 字段异常，跳过")
        return 1

    state = load_state()
    seen: dict[str, Any] = state.get("airdrops") or {}
    alerts: dict[str, Any] = state.get("alerts") or {}
    new_seen: dict[str, Any] = {}
    pushed = 0
    skipped_capped = 0

    for item in airdrops:
        if not isinstance(item, dict):
            continue
        key = airdrop_key(item)
        new_seen[key] = item
        prev = seen.get(key)

        if prev is None:
            item["market_check"] = market_circulation_check(item)
            title = "✨ 新增 Alpha 空投"
            html = fmt_item_html(item)
            text = fmt_item_text(item)
            log(f"NEW {key}: {item.get('token')} {fmt_when(item)} market={item['market_check'].get('label')}")
            change_type = "new"
        else:
            diffs = diff_item(prev, item)
            if not diffs:
                continue
            time_just_set = (
                (prev.get("time") in (None, "")) and (item.get("time") not in (None, ""))
            )
            if time_just_set:
                title = "⏰ Alpha 空投时间已确认"
                change_type = "time_confirmed"
            else:
                title = "🔄 Alpha 空投信息更新"
                change_type = "update"
            item["market_check"] = market_circulation_check(item)
            change_html = "<br>".join(f"• {d}" for d in diffs)
            change_text = "\n".join(f"• {d}" for d in diffs)
            html = fmt_item_html(item) + "<br><br><b>变更:</b><br>" + change_html
            text = fmt_item_text(item) + "\n\n变更:\n" + change_text
            log(f"UPDATE[{change_type}] {key}: {' | '.join(diffs)} market={item['market_check'].get('label')}")

        # 上限检查
        rec = alerts.get(key) or {"count": 0, "history": []}
        if ALERT_CAP > 0 and rec["count"] >= ALERT_CAP:
            log(f"SKIP {key}: 已达提醒上限 {ALERT_CAP}")
            skipped_capped += 1
            continue

        ok = dispatch(title, html, text)
        if ok:
            pushed += 1
            rec["count"] = int(rec.get("count", 0)) + 1
            rec["history"] = (rec.get("history") or []) + [
                {
                    "ts": int(time.time()),
                    "change_type": change_type,
                    "title": title,
                }
            ]
            rec["history"] = rec["history"][-10:]  # 只保留最近 10 条
            alerts[key] = rec
            log(f"PUSHED {key} ({rec['count']}/{ALERT_CAP if ALERT_CAP>0 else '∞'})")
        else:
            log(f"PUSH_FAIL {key}: 推送失败，本次不计数")

        time.sleep(0.5)

    # ---------- 第二阶段：临近开抢提醒（独立于 3 次上限） ----------
    now_utc = datetime.now(timezone.utc)
    imminent_pushed = 0
    for key, item in new_seen.items():
        dt = parse_scheduled_dt(item)
        if not dt:
            continue
        delta = (dt - now_utc).total_seconds()
        rec = alerts.get(key) or {"count": 0, "history": []}
        if rec.get("imminent_pushed"):
            continue
        # 落在 [-grace, +window] 窗口内才推；过早 / 过晚都跳过
        if delta > IMMINENT_WINDOW_SEC:
            continue
        if delta < -IMMINENT_GRACE_SEC:
            continue
        # 计算文案：开抢前 / 开抢中
        if delta >= 0:
            mins = int(delta // 60)
            human = f"还有约 {mins} 分钟开抢"
        else:
            mins = int((-delta) // 60)
            human = f"已开抢约 {mins} 分钟"
        if not item.get("market_check"):
            item["market_check"] = market_circulation_check(item)
        title = "🔥 Alpha 空投临近开抢"
        html = fmt_item_html(item) + f"<br><br><b>{human}</b>"
        text = fmt_item_text(item) + f"\n\n{human}"
        ok = dispatch(title, html, text)
        if ok:
            rec["imminent_pushed"] = True
            rec["imminent_pushed_at"] = int(time.time())
            rec["history"] = (rec.get("history") or []) + [
                {
                    "ts": int(time.time()),
                    "change_type": "imminent",
                    "title": title,
                }
            ]
            rec["history"] = rec["history"][-10:]
            alerts[key] = rec
            imminent_pushed += 1
            log(f"IMMINENT_PUSHED {key} delta={int(delta)}s")
        else:
            log(f"IMMINENT_PUSH_FAIL {key}")
        time.sleep(0.5)

    state["airdrops"] = new_seen
    # 删掉那些已经从 API 里消失的 alerts（释放内存）
    state["alerts"] = {k: v for k, v in alerts.items() if k in new_seen}

    stability_pushed = monitor_stability(state)
    save_state(state)

    if pushed or skipped_capped or imminent_pushed or stability_pushed:
        print(f"PUSHED {pushed} CAPPED {skipped_capped} IMMINENT {imminent_pushed} STABILITY {stability_pushed}")
    else:
        print("NO_CHANGE")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in {"--mark-stability-done-today", "--stability-done-today"}:
        RUNTIME.mkdir(parents=True, exist_ok=True)
        st = load_state()
        mark_stability_trading_done_today(st)
        save_state(st)
        print(f"STABILITY_TRADING_DONE {st['stability_trading_done_date']}")
        raise SystemExit(0)
    raise SystemExit(main())
