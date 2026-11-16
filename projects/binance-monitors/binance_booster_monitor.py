#!/usr/bin/env python3
"""Binance Web3 Booster / Wallet activity monitor.

Purpose:
- Monitor Binance official announcement APIs for Binance Wallet / Web3 Booster / Megadrop / Airdrop / Alpha related activity.
- Probe the Web3 Booster page status without relying on DOM scraping (the page is often WAF/region blocked).
- Alert only on new relevant items via Telegram/WxPusher-compatible Telegram runtime.

State: runtime/binance_booster_state.json
Log:   runtime/binance_booster_monitor.log

Env:
- BOOSTER_MONITOR_PUSH=1|0          default 0
- BOOSTER_MONITOR_CHANNEL=telegram|wxpusher|both  default wxpusher
- BOOSTER_MONITOR_NOTIFY_INITIAL=0  default 0; first run baselines old items silently
- BOOSTER_MONITOR_VERBOSE=1         print to stderr
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
RUNTIME = Path(os.environ.get("BINANCE_MONITOR_RUNTIME", str(ROOT / "runtime"))).expanduser()
STATE_PATH = RUNTIME / "binance_booster_state.json"
LOG_PATH = RUNTIME / "binance_booster_monitor.log"
WXPUSHER_CONFIG_PATH = RUNTIME / "wxpusher_config.json"
WXPUSHER_API = "https://wxpusher.zjiecode.com/api/send/message"

PUSH = os.getenv("BOOSTER_MONITOR_PUSH", "0") == "1"
CHANNEL = os.getenv("BOOSTER_MONITOR_CHANNEL", "wxpusher").strip().lower()
NOTIFY_INITIAL = os.getenv("BOOSTER_MONITOR_NOTIFY_INITIAL", "0") == "1"
VERBOSE = os.getenv("BOOSTER_MONITOR_VERBOSE", "0") == "1"
MONITOR_ALL = os.getenv("BOOSTER_MONITOR_ALL", "1") == "1"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "lang": "zh-CN",
    "clienttype": "web",
}

BINANCE_CATALOGS = {
    48: "新币上线",
    49: "币安最新消息",
    93: "最新活动",
    50: "法币上线",
    161: "下架公告",
    157: "维护更新",
    51: "API更新",
    128: "加密货币空投",
}
ANNOUNCE_LIST = "https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query"
ANNOUNCE_DETAIL = "https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query"
BOOSTER_URL = "https://web3.binance.com/booster?chain=bsc"
BOOSTER_EN_URL = "https://web3.binance.com/en/booster?chain=bsc"

# primary terms: trigger directly when present.
# Keep this intentionally narrow; generic Binance Alpha trading competitions are already covered by binance_alpha_monitor.py.
PRIMARY_RE = re.compile(
    r"(\b(booster|web3\s*booster|wallet\s*booster|binance\s*wallet|web3\s*wallet|megadrop)\b|币安钱包|Web3\s*API|Megadrop)",
    re.I,
)
# contextual match: require task/airdrop/reward wording plus Web3/wallet/BSC context.
AIRDROP_CONTEXT_RE = re.compile(r"\b(airdrop|quest|campaign|reward|rewards|task|tasks)\b", re.I)
WEB3_CONTEXT_RE = re.compile(r"\b(web3|wallet|bsc|bnb\s*chain|booster|megadrop)\b", re.I)
CN_RE = re.compile(r"(币安钱包|Web3\s*API|booster|megadrop|Megadrop)", re.I)


def log(msg: str) -> None:
    from telegram_runtime import redact_log
    msg = redact_log(msg)
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass
    if VERBOSE:
        print(line, file=sys.stderr)


def fetch_json(url: str, timeout: int = 15) -> Any:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as first_exc:
        # Retry direct when global proxy path is broken.
        log(f"fetch_json failed once, retry direct: {url[:100]} | {first_exc}")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"seen": {}, "last_run_ts": 0, "booster_probe": {}}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("seen", {})
            data.setdefault("booster_probe", {})
            return data
    except Exception:
        pass
    return {"seen": {}, "last_run_ts": 0, "booster_probe": {}}


def save_state(st: dict[str, Any]) -> None:
    st["last_run_ts"] = int(time.time())
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def body_to_text(body: Any) -> str:
    if not body:
        return ""
    if isinstance(body, str):
        # Binance detail body is often JSON-as-string with node/child tree.
        try:
            obj = json.loads(body)
            return body_to_text(obj)
        except Exception:
            s = re.sub(r"<[^>]+>", " ", body)
            return html.unescape(re.sub(r"\s+", " ", s)).strip()
    if isinstance(body, dict):
        parts: list[str] = []
        for key in ("text", "value", "content"):
            v = body.get(key)
            if isinstance(v, str):
                parts.append(v)
        for key in ("child", "children"):
            v = body.get(key)
            if isinstance(v, list):
                parts.extend(body_to_text(x) for x in v)
        return " ".join(p for p in parts if p).strip()
    if isinstance(body, list):
        return " ".join(body_to_text(x) for x in body if x).strip()
    return ""


def is_relevant(title: str, detail_text: str = "") -> tuple[bool, str]:
    if MONITOR_ALL:
        return True, "all_binance_announcement"
    blob = f"{title}\n{detail_text}"
    if PRIMARY_RE.search(blob):
        return True, "primary_keyword"
    if AIRDROP_CONTEXT_RE.search(blob) and WEB3_CONTEXT_RE.search(blob):
        return True, "airdrop_web3_context"
    # Chinese fallback: only narrow Binance Wallet / Web3 API / Booster / Megadrop terms.
    if CN_RE.search(blob):
        return True, "cn_primary_keyword"
    return False, "no_match"


def article_url(code: str) -> str:
    return f"https://www.binance.com/zh-CN/support/announcement/{urllib.parse.quote(code)}"


def fetch_candidates() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for cid, cname in BINANCE_CATALOGS.items():
        url = f"{ANNOUNCE_LIST}?catalogId={cid}&pageNo=1&pageSize=25"
        try:
            data = fetch_json(url)
        except Exception as exc:
            log(f"catalog fetch failed {cid}/{cname}: {exc}")
            continue
        articles = (((data or {}).get("data") or {}).get("articles") or [])
        for a in articles:
            if not isinstance(a, dict):
                continue
            code = str(a.get("code") or "").strip()
            title = str(a.get("title") or "").strip()
            if not code or not title or code in seen_codes:
                continue
            seen_codes.add(code)
            # Detail only for strong title hits; keeps each scheduled run short and avoids WAF/timeout buildup.
            detail_text = ""
            quick_relevant, quick_reason = is_relevant(title, "")
            if PRIMARY_RE.search(title) or re.search(r"web3|wallet|booster|megadrop|币安钱包", title, re.I):
                try:
                    detail = fetch_json(f"{ANNOUNCE_DETAIL}?articleCode={urllib.parse.quote(code)}", timeout=8)
                    body = ((detail or {}).get("data") or {}).get("body")
                    detail_text = body_to_text(body)[:3000]
                except Exception as exc:
                    log(f"detail fetch failed {code}: {exc}")
            relevant, reason = is_relevant(title, detail_text)
            if relevant:
                out.append({
                    "key": f"article:{code}",
                    "type": "binance_announcement",
                    "catalog_id": cid,
                    "catalog": cname,
                    "code": code,
                    "title": title,
                    "url": article_url(code),
                    "reason": reason if reason != "no_match" else quick_reason,
                    "snippet": detail_text[:500],
                    "fetched_at": int(time.time()),
                })
    return out


def probe_booster_page() -> dict[str, Any]:
    # This is not an alert source by itself; it records whether the page is accessible/WAF-blocked.
    result = {"ok": False, "status": None, "url": BOOSTER_URL, "note": ""}
    for url in (BOOSTER_URL, BOOSTER_EN_URL):
        req = urllib.request.Request(url, headers={**HEADERS, "Accept": "text/html,*/*"})
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                body = resp.read(5000).decode("utf-8", errors="replace")
                result = {"ok": 200 <= resp.status < 300, "status": resp.status, "url": url, "note": body[:300]}
                if result["ok"] and "x-amzn-waf-action" not in str(resp.headers).lower():
                    return result
        except Exception as exc:
            result = {"ok": False, "status": None, "url": url, "note": str(exc)[:300]}
    return result


def load_wxpusher_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {"app_token": "", "uids": [], "topic_ids": []}
    if WXPUSHER_CONFIG_PATH.exists():
        try:
            data = json.loads(WXPUSHER_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update(data)
        except Exception as exc:
            log(f"wxpusher config parse failed: {exc}")
    if os.getenv("WXPUSHER_APP_TOKEN"):
        cfg["app_token"] = os.getenv("WXPUSHER_APP_TOKEN", "")
    if os.getenv("WXPUSHER_UIDS"):
        cfg["uids"] = [x.strip() for x in os.getenv("WXPUSHER_UIDS", "").split(",") if x.strip()]
    return cfg


def wxpusher_send(title: str, html_body: str) -> bool:
    cfg = load_wxpusher_config()
    token = str(cfg.get("app_token") or "").strip()
    uids = [x for x in (cfg.get("uids") or []) if x]
    topic_ids = cfg.get("topic_ids") or []
    if not token or (not uids and not topic_ids):
        log("wxpusher token/uids missing")
        return False
    payload = {
        "appToken": token,
        "summary": title[:20],
        "content": html_body,
        "contentType": 2,
        "uids": uids,
        "topicIds": topic_ids,
        "url": BOOSTER_URL,
        "verifyPay": False,
    }
    payload_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        WXPUSHER_API,
        data=payload_bytes,
        headers={"Content-Type": "application/json"},
    )

    def _parse_result(body: str) -> bool:
        try:
            data = json.loads(body)
        except Exception:
            data = {"raw": body}
        ok = bool(data.get("success")) or data.get("code") == 1000
        if not ok:
            log(f"wxpusher send failed response: {body[:300]}")
        return bool(ok)

    def _send(open_fn) -> bool:
        with open_fn(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        return _parse_result(body)

    def _send_with_curl(no_proxy: bool = False) -> bool:
        cmd = [
            "curl", "-sS", "--connect-timeout", "10", "--max-time", "20",
            "-H", "Content-Type: application/json",
            "--data-binary", "@-",
            WXPUSHER_API,
        ]
        env = os.environ.copy()
        if no_proxy:
            for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
                env.pop(k, None)
        cp = subprocess.run(cmd, input=payload_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=25)
        if cp.returncode != 0:
            log(f"wxpusher curl {'direct ' if no_proxy else ''}failed rc={cp.returncode}: {cp.stderr.decode('utf-8', 'replace')[:300]}")
            return False
        return _parse_result(cp.stdout.decode("utf-8", errors="replace"))

    try:
        return _send(urllib.request.urlopen)
    except Exception as exc:
        log(f"wxpusher send exception, retry direct urllib: {exc}")
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            return _send(opener.open)
        except Exception as exc2:
            log(f"wxpusher direct urllib exception, retry curl: {exc2}")
            if _send_with_curl(no_proxy=False):
                log("wxpusher curl fallback ok")
                return True
            if _send_with_curl(no_proxy=True):
                log("wxpusher curl direct fallback ok")
                return True
            return False



def translate_to_zh(text: str, max_chars: int = 900) -> str:
    """Translate English-ish announcement text to Simplified Chinese using Google public endpoint.
    Best-effort; falls back to original text if blocked.
    """
    text = (text or "").strip()
    if not text:
        return ""
    # If it already contains enough CJK, keep it.
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    if cjk >= max(6, len(text) * 0.15):
        return text[:max_chars]
    src = text[:max_chars]
    try:
        q = urllib.parse.urlencode({
            "client": "gtx",
            "sl": "auto",
            "tl": "zh-CN",
            "dt": "t",
            "q": src,
        })
        url = "https://translate.googleapis.com/translate_a/single?" + q
        data = fetch_json(url, timeout=8)
        if isinstance(data, list) and data and isinstance(data[0], list):
            out = "".join(str(seg[0]) for seg in data[0] if isinstance(seg, list) and seg)
            return out.strip() or src
    except Exception as exc:
        log(f"translate_to_zh failed: {exc}")
    return src

def telegram_send(text: str) -> bool:
    sys.path.insert(0, str(ROOT))
    try:
        from telegram_runtime import load_telegram_runtime  # type: ignore
    except Exception as exc:
        log(f"telegram_runtime import failed: {exc}")
        return False
    bot, chat = load_telegram_runtime()
    if not bot or not chat:
        log("telegram runtime missing bot/chat")
        return False
    payload = json.dumps({
        "chat_id": chat,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as exc:
        log(f"telegram send failed, retry direct: {exc}")
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=10) as resp:
                return resp.status == 200
        except Exception as exc2:
            log(f"telegram direct send failed: {exc2}")
            return False


def fmt_alert(item: dict[str, Any], html_mode: bool = True) -> str:
    esc = html.escape if html_mode else (lambda x: x)
    raw_title = str(item.get("title") or "?")
    raw_snippet = str(item.get("snippet") or "")[:700]
    zh_title_raw = translate_to_zh(raw_title, 180)
    zh_snippet_raw = translate_to_zh(raw_snippet, 500) if raw_snippet else ""
    title = esc(zh_title_raw)
    original_title = esc(raw_title)
    catalog = esc(str(item.get("catalog") or "?"))
    url = esc(str(item.get("url") or ""))
    reason = esc(str(item.get("reason") or ""))
    snippet = esc(zh_snippet_raw[:500])
    if html_mode:
        lines = [
            "<h3>🚨 币安公告监控命中新公告</h3>",
            f"<b>{title}</b>",
            (f"<br>原标题: {original_title}" if original_title != title else ""),
            f"<br>来源: {catalog}",
            f"<br>命中: {reason}",
        ]
        if snippet:
            lines.append(f"<br>摘要: {snippet}")
        if url:
            lines.append(f"<br>链接: <a href=\"{url}\">打开公告</a>")
        lines.append("<br><br>动作建议: 优先检查是否涉及上线、空投、活动、维护、下架或 API 变更；有交易/交互要求时先评估成本和风险。")
        return "".join(lines)
    lines = [
        "🚨 币安公告监控命中新公告",
        title,
        (f"原标题: {original_title}" if original_title != title else ""),
        f"来源: {catalog}",
        f"命中: {reason}",
    ]
    if snippet:
        lines.append(f"摘要: {snippet}")
    if url:
        lines.append(f"链接: {url}")
    lines.append("\n动作建议: 优先检查是否涉及上线、空投、活动、维护、下架或 API 变更；有交易/交互要求时先评估成本和风险。")
    return "\n".join(lines)


def enabled_channels() -> list[str]:
    if CHANNEL == "both":
        return ["wxpusher", "telegram"]
    if CHANNEL in ("wxpusher", "telegram"):
        return [CHANNEL]
    log(f"unknown channel {CHANNEL}")
    return []


def dispatch_channels(item: dict[str, Any], channels: list[str]) -> dict[str, bool]:
    results: dict[str, bool] = {}
    for ch in channels:
        if ch == "wxpusher":
            results[ch] = wxpusher_send("币安新公告", fmt_alert(item, html_mode=True))
        elif ch == "telegram":
            results[ch] = telegram_send(fmt_alert(item, html_mode=False))
    return results


def main() -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    st = load_state()
    first_run = not bool(st.get("seen"))
    seen: dict[str, Any] = st.get("seen") or {}

    probe = probe_booster_page()
    st["booster_probe"] = {**probe, "ts": int(time.time())}
    log(f"probe ok={probe.get('ok')} status={probe.get('status')} url={probe.get('url')} note={str(probe.get('note'))[:80]}")

    candidates = fetch_candidates()
    log(f"candidates={len(candidates)} first_run={first_run}")

    pushed = 0
    push_failed: dict[str, Any] = st.get("push_failed") or {}
    sent_channels: dict[str, list[str]] = st.get("sent_channels") or {}
    channels = enabled_channels()
    for item in candidates:
        key = item["key"]
        if key in seen:
            continue
        if first_run and not NOTIFY_INITIAL:
            seen[key] = item
            log(f"baseline {key} {item.get('title')}")
            continue
        if PUSH:
            already_sent = set(sent_channels.get(key) or [])
            pending_channels = [ch for ch in channels if ch not in already_sent]
            results = dispatch_channels(item, pending_channels)
            ok_channels = {ch for ch, ok in results.items() if ok}
            if ok_channels:
                sent_channels[key] = sorted(already_sent | ok_channels)
            if channels and set(sent_channels.get(key) or []) >= set(channels):
                seen[key] = item
                push_failed.pop(key, None)
                pushed += 1
                log(f"pushed {key} channel={CHANNEL}")
            else:
                pending_after = [ch for ch in channels if ch not in set(sent_channels.get(key) or [])]
                push_failed[key] = {**item, "failed_at": int(time.time()), "pending_channels": pending_after}
                log(f"push partial/failed {key} channel={CHANNEL} sent={sent_channels.get(key)} pending={pending_after}")
        else:
            seen[key] = item
            log(f"push disabled/new {key} {item.get('title')}")

    st["seen"] = seen
    st["push_failed"] = push_failed
    st["sent_channels"] = sent_channels
    st["last_candidates"] = candidates[:20]
    st["last_pushed"] = pushed
    save_state(st)
    print(json.dumps({"candidates": len(candidates), "new_pushed": pushed, "first_run": first_run, "probe": probe}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
