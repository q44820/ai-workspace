#!/usr/bin/env python3
"""Meme 币价格区间监控。

默认通过 DexScreener 按交易对读取配置指定方向的币价；价格离开用户配置的区间时，
通过 WxPusher 提醒。仅在状态变化或越界冷却期到期后提醒，避免刷屏。

用法：
  cp scripts/meme_lp_price_monitor.example.json scripts/meme_lp_price_monitor.json
  # 修改配置后测试
  python3 scripts/meme_lp_price_monitor.py --once --dry-run --verbose
  # 持续运行
  python3 scripts/meme_lp_price_monitor.py

WxPusher 优先读取环境变量 WXPUSHER_APP_TOKEN、WXPUSHER_UIDS、
WXPUSHER_TOPIC_IDS；未提供时复用同目录 wxpusher_config.json。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = ROOT / "meme_lp_price_monitor.json"
DEFAULT_WX_CONFIG_PATH = ROOT / "wxpusher_config.json"
WXPUSHER_API = "https://wxpusher.zjiecode.com/api/send/message"
DEXSCREENER_PAIR_API = "https://api.dexscreener.com/latest/dex/pairs/{chain}/{pair}"
GECKOTERMINAL_POOL_API = "https://api.geckoterminal.com/api/v2/networks/{chain}/pools/{pair}"
USER_AGENT = "meme-lp-price-monitor/1.0"
ROBINHOOD_POSITION_MANAGER = "0x58daec3116aae6d93017baaea7749052e8a04fa7"
ROBINHOOD_POOL_MANAGER = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
ROBINHOOD_RPC_URLS = (
    "https://rpc.mainnet.chain.robinhood.com",
    "https://robinhood-rpc.publicnode.com",
    "https://rpc.ordofi.network",
)
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


class MonitorError(RuntimeError):
    pass


def parse_decimal(value: Any, field: str) -> Decimal:
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise MonitorError(f"{field} 不是有效数字") from exc
    if not number.is_finite():
        raise MonitorError(f"{field} 必须是有限数字")
    return number


def classify_price(price: Decimal, lower: Decimal, upper: Decimal) -> str:
    """边界本身视为仍在区间内。"""
    if price < lower:
        return "below"
    if price > upper:
        return "above"
    return "inside"


def fmt_decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def csv_items(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def csv_ints(value: str | None) -> list[int]:
    result: list[int] = []
    for item in csv_items(value):
        try:
            result.append(int(item))
        except ValueError as exc:
            raise MonitorError("WXPUSHER_TOPIC_IDS 必须是逗号分隔的整数") from exc
    return result


def load_json(path: Path, required: bool = True) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise MonitorError(f"配置文件不存在：{path}")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MonitorError(f"无法读取 JSON：{path}：{exc}") from exc
    if not isinstance(data, dict):
        raise MonitorError(f"JSON 顶层必须是对象：{path}")
    return data


def normalize_config(raw: dict[str, Any], config_path: Path) -> dict[str, Any]:
    monitors = raw.get("monitors")
    if not isinstance(monitors, list):
        raise MonitorError("配置中的 monitors 必须是数组（空仓允许空数组）")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(monitors, start=1):
        if not isinstance(item, dict):
            raise MonitorError(f"monitors[{index}] 必须是对象")
        name = str(item.get("name") or f"monitor-{index}").strip()
        chain = str(item.get("chain") or "").strip().lower()
        pair_address = str(item.get("pair_address") or "").strip()
        if not chain or not pair_address:
            raise MonitorError(f"{name} 缺少 chain 或 pair_address")
        default_key = f"{chain}:position:{item['position_id']}" if item.get("position_id") is not None else f"{chain}:{pair_address.lower()}"
        key = str(item.get("id") or default_key)
        if key in seen:
            raise MonitorError(f"监控 ID 重复：{key}")
        seen.add(key)
        lower = parse_decimal(item.get("lower"), f"{name}.lower")
        upper = parse_decimal(item.get("upper"), f"{name}.upper")
        if lower < 0 or upper <= lower:
            raise MonitorError(f"{name} 的区间无效，必须满足 0 <= lower < upper")
        normalized.append(
            {
                "id": key,
                "name": name,
                "chain": chain,
                "pair_address": pair_address,
                "token_address": str(item.get("token_address") or "").strip().lower(),
                "position_id": int(item["position_id"]) if item.get("position_id") is not None else None,
                "lower": lower,
                "upper": upper,
                "enabled": bool(item.get("enabled", True)),
                "price_field": str(item.get("price_field") or "priceUsd").strip(),
                "invert_price": bool(item.get("invert_price", False)),
                "price_unit": str(item.get("price_unit") or "").strip(),
                "expected_base_symbol": str(item.get("expected_base_symbol") or "").strip(),
                "expected_quote_symbol": str(item.get("expected_quote_symbol") or "").strip(),
                "expected_dex": str(item.get("expected_dex") or "").strip().lower(),
                "expected_label": str(item.get("expected_label") or "").strip().lower(),
            }
        )
        if normalized[-1]["price_field"] not in {"priceUsd", "priceNative"}:
            raise MonitorError(f"{name}.price_field 仅支持 priceUsd 或 priceNative")

    interval = int(raw.get("poll_interval_seconds", 30))
    cooldown = int(raw.get("repeat_alert_seconds", 1800))
    error_cooldown = int(raw.get("error_alert_seconds", 3600))
    if interval < 10:
        raise MonitorError("poll_interval_seconds 不能小于 10 秒")
    if cooldown < 0 or error_cooldown < 0:
        raise MonitorError("提醒冷却时间不能为负数")

    state_value = raw.get("state_file", "meme_lp_price_monitor_state.json")
    state_path = Path(str(state_value)).expanduser()
    if not state_path.is_absolute():
        state_path = config_path.parent / state_path

    return {
        "poll_interval_seconds": interval,
        "repeat_alert_seconds": cooldown,
        "error_alert_seconds": error_cooldown,
        "state_path": state_path,
        "wallet": str(raw.get("wallet") or "").lower(),
        "monitors": normalized,
    }


def load_wxpusher(config_path: Path) -> dict[str, Any]:
    app_token = os.getenv("WXPUSHER_APP_TOKEN", "").strip()
    uids = csv_items(os.getenv("WXPUSHER_UIDS"))
    topic_ids = csv_ints(os.getenv("WXPUSHER_TOPIC_IDS"))

    fallback = load_json(config_path, required=False)
    if not app_token:
        app_token = str(fallback.get("app_token") or "").strip()
    if not uids:
        raw_uids = fallback.get("uids", [])
        if isinstance(raw_uids, list):
            uids = [str(value).strip() for value in raw_uids if str(value).strip()]
    if not topic_ids:
        raw_topics = fallback.get("topic_ids", [])
        if isinstance(raw_topics, list):
            try:
                topic_ids = [int(value) for value in raw_topics]
            except (TypeError, ValueError) as exc:
                raise MonitorError("wxpusher_config.json 的 topic_ids 必须是整数数组") from exc

    if not app_token:
        raise MonitorError("缺少 WxPusher appToken，请设置环境变量或 wxpusher_config.json")
    if not uids and not topic_ids:
        raise MonitorError("缺少 WxPusher 接收 UID 或 Topic ID")
    return {"app_token": app_token, "uids": uids, "topic_ids": topic_ids}


def request_json(url: str, method: str = "GET", payload: dict[str, Any] | None = None, timeout: int = 15) -> Any:
    body = None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
    except Exception as first_exc:
        # 本机默认走代理；代理不可用时按工作区规则直连重试一次。
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", errors="replace")
        except Exception as direct_exc:
            raise MonitorError(f"网络请求失败：{direct_exc}") from first_exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise MonitorError("接口返回的不是有效 JSON") from exc


def fetch_dexscreener_price(monitor: dict[str, Any]) -> dict[str, Any]:
    chain = urllib.parse.quote(monitor["chain"], safe="")
    pair = urllib.parse.quote(monitor["pair_address"], safe="")
    data = request_json(DEXSCREENER_PAIR_API.format(chain=chain, pair=pair))
    pairs = data.get("pairs") if isinstance(data, dict) else None
    if not isinstance(pairs, list) or not pairs:
        raise MonitorError("DexScreener 未找到该链上的交易对")
    exact = next(
        (
            value
            for value in pairs
            if isinstance(value, dict)
            and str(value.get("pairAddress", "")).lower() == monitor["pair_address"].lower()
        ),
        None,
    )
    if exact is None:
        raise MonitorError("DexScreener 返回结果中没有精确匹配的交易对")

    base_raw = exact.get("baseToken")
    quote_raw = exact.get("quoteToken")
    labels_raw = exact.get("labels")
    base: dict[str, Any] = base_raw if isinstance(base_raw, dict) else {}
    quote: dict[str, Any] = quote_raw if isinstance(quote_raw, dict) else {}
    labels = [str(value).lower() for value in labels_raw] if isinstance(labels_raw, list) else []
    base_symbol = str(base.get("symbol") or "")
    quote_symbol = str(quote.get("symbol") or "")
    dex = str(exact.get("dexId") or "").lower()
    if monitor.get("expected_base_symbol") and base_symbol != monitor["expected_base_symbol"]:
        raise MonitorError(f"LP 基础币不匹配：期望 {monitor['expected_base_symbol']}，实际 {base_symbol or '未知'}")
    if monitor.get("expected_quote_symbol") and quote_symbol != monitor["expected_quote_symbol"]:
        raise MonitorError(f"LP 报价币不匹配：期望 {monitor['expected_quote_symbol']}，实际 {quote_symbol or '未知'}")
    if monitor.get("expected_dex") and dex != monitor["expected_dex"]:
        raise MonitorError(f"DEX 不匹配：期望 {monitor['expected_dex']}，实际 {dex or '未知'}")
    if monitor.get("expected_label") and monitor["expected_label"] not in labels:
        raise MonitorError(f"LP 版本不匹配：缺少标签 {monitor['expected_label']}")

    price_field = monitor.get("price_field", "priceUsd")
    price = parse_decimal(exact.get(price_field), f"{monitor['name']}.{price_field}")
    if price < 0:
        raise MonitorError("DexScreener 返回了负价格")
    if monitor.get("invert_price"):
        if price == 0:
            raise MonitorError("DexScreener 返回价格为 0，无法换算报价方向")
        price = Decimal(1) / price
    liquidity_raw = exact.get("liquidity")
    liquidity: dict[str, Any] = liquidity_raw if isinstance(liquidity_raw, dict) else {}
    return {
        "price": price,
        "base_symbol": base_symbol or monitor["name"],
        "quote_symbol": quote_symbol or "USD",
        "dex": str(exact.get("dexId") or "DEX"),
        "url": str(exact.get("url") or ""),
        "liquidity_usd": liquidity.get("usd"),
        "source": "DexScreener",
    }


def fetch_geckoterminal_price(monitor: dict[str, Any]) -> dict[str, Any]:
    url = GECKOTERMINAL_POOL_API.format(
        chain=urllib.parse.quote(monitor["chain"]),
        pair=urllib.parse.quote(monitor["pair_address"]),
    )
    data = request_json(url)
    attributes = ((data.get("data") or {}).get("attributes") or {}) if isinstance(data, dict) else {}
    price = parse_decimal(attributes.get("base_token_price_usd"), f"{monitor['name']}.base_token_price_usd")
    if price <= 0:
        raise MonitorError("GeckoTerminal 返回价格无效")
    name = str(attributes.get("name") or "")
    symbols = [part.strip() for part in name.split("/")]
    base_symbol = symbols[0] if symbols and symbols[0] else monitor.get("expected_base_symbol") or monitor["name"]
    quote_symbol = symbols[1] if len(symbols) > 1 and symbols[1] else monitor.get("expected_quote_symbol") or "USD"
    return {
        "price": price,
        "base_symbol": base_symbol,
        "quote_symbol": quote_symbol,
        "dex": "GeckoTerminal",
        "url": f"https://www.geckoterminal.com/{monitor['chain']}/pools/{monitor['pair_address']}",
        "liquidity_usd": attributes.get("reserve_in_usd"),
        "source": "GeckoTerminal",
    }


def function_selector(signature: str) -> str:
    try:
        from Crypto.Hash import keccak
    except ImportError as exc:
        raise MonitorError("缺少链上价格回退所需的 Crypto.Hash") from exc
    digest = keccak.new(digest_bits=256)
    digest.update(signature.encode("utf-8"))
    return digest.hexdigest()[:8]


def encode_uint256(value: int) -> str:
    return hex(value)[2:].rjust(64, "0")


def rpc_call(url: str, method: str, params: list[Any]) -> Any:
    data = request_json(
        url,
        method="POST",
        payload={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=10,
    )
    if not isinstance(data, dict) or "result" not in data:
        detail = data.get("error") if isinstance(data, dict) else "返回格式异常"
        raise MonitorError(f"RPC 返回失败：{detail}")
    return data["result"]


def eth_call_rpc(url: str, to: str, data: str) -> str:
    result = rpc_call(url, "eth_call", [{"to": to, "data": data}, "latest"])
    if not isinstance(result, str) or not result.startswith("0x"):
        raise MonitorError("RPC eth_call 返回格式异常")
    return result


def abi_words(data: str) -> list[str]:
    raw = data[2:] if data.startswith("0x") else data
    return [raw[index:index + 64] for index in range(0, len(raw), 64) if len(raw[index:index + 64]) == 64]


def decode_int24(word: str) -> int:
    value = int(word, 16) & ((1 << 24) - 1)
    return value - (1 << 24) if value & (1 << 23) else value


def token_decimals_rpc(url: str, address: str) -> int:
    if address.lower() == ZERO_ADDRESS:
        return 18
    raw = eth_call_rpc(url, address, "0x" + function_selector("decimals()"))
    value = int(raw, 16)
    if not 0 <= value <= 36:
        raise MonitorError(f"代币 decimals 异常：{value}")
    return value


def pool_id_for_key(currency0: str, currency1: str, fee: int, tick_spacing: int, hooks: str) -> str:
    try:
        from Crypto.Hash import keccak
    except ImportError as exc:
        raise MonitorError("缺少链上价格回退所需的 Crypto.Hash") from exc
    tick_word = ((1 << 256) + tick_spacing if tick_spacing < 0 else tick_spacing)
    encoded = "".join(
        [
            currency0[2:].rjust(64, "0"),
            currency1[2:].rjust(64, "0"),
            encode_uint256(fee),
            encode_uint256(tick_word),
            hooks[2:].rjust(64, "0"),
        ]
    )
    digest = keccak.new(digest_bits=256)
    digest.update(bytes.fromhex(encoded))
    return digest.hexdigest()


def fetch_robinhood_onchain_price(monitor: dict[str, Any], rpc_url: str) -> dict[str, Any]:
    position_id = monitor.get("position_id")
    token_address = str(monitor.get("token_address") or "").lower()
    if position_id is None or not token_address:
        raise MonitorError("缺少 position_id 或 token_address，无法使用链上价格回退")

    position_data = "0x" + function_selector("getPoolAndPositionInfo(uint256)") + encode_uint256(int(position_id))
    position_raw = eth_call_rpc(rpc_url, ROBINHOOD_POSITION_MANAGER, position_data)
    words = abi_words(position_raw)
    if len(words) < 5:
        raise MonitorError("链上仓位数据长度异常")
    currency0 = "0x" + words[0][-40:].lower()
    currency1 = "0x" + words[1][-40:].lower()
    fee = int(words[2], 16)
    tick_spacing = decode_int24(words[3])
    hooks = "0x" + words[4][-40:].lower()
    if token_address not in {currency0, currency1}:
        raise MonitorError("配置代币不属于该链上 LP")

    pool_id = pool_id_for_key(currency0, currency1, fee, tick_spacing, hooks)
    slot_data = "0x" + function_selector("extsload(bytes32)") + pool_id
    slot_raw = eth_call_rpc(rpc_url, ROBINHOOD_POOL_MANAGER, slot_data)
    slot_words = abi_words(slot_raw)
    if not slot_words:
        raise MonitorError("链上池状态为空")
    sqrt_price_x96 = int(slot_words[0], 16) & ((1 << 160) - 1)
    if sqrt_price_x96 <= 0:
        raise MonitorError("链上池价格为 0")

    decimals0 = token_decimals_rpc(rpc_url, currency0)
    decimals1 = token_decimals_rpc(rpc_url, currency1)
    token1_per_token0 = (
        Decimal(sqrt_price_x96) * Decimal(sqrt_price_x96) / Decimal(2 ** 192)
    ) * (Decimal(10) ** (decimals0 - decimals1))
    price = token1_per_token0 if token_address == currency0 else Decimal(1) / token1_per_token0
    endpoint_name = urllib.parse.urlparse(rpc_url).netloc
    return {
        "price": price,
        "base_symbol": monitor.get("expected_base_symbol") or monitor["name"],
        "quote_symbol": monitor.get("expected_quote_symbol") or "USD",
        "dex": "Uniswap v4 链上",
        "url": "",
        "liquidity_usd": None,
        "source": f"Robinhood RPC · {endpoint_name}",
    }


def fetch_pair_price(monitor: dict[str, Any]) -> dict[str, Any]:
    """DexScreener 优先，随后 GeckoTerminal，最后切换多个独立 Robinhood RPC。"""
    errors: list[str] = []
    try:
        return fetch_dexscreener_price(monitor)
    except Exception as exc:
        errors.append(f"DexScreener: {exc}")

    try:
        return fetch_geckoterminal_price(monitor)
    except Exception as exc:
        errors.append(f"GeckoTerminal: {exc}")

    if monitor.get("chain") == "robinhood":
        for rpc_url in ROBINHOOD_RPC_URLS:
            try:
                return fetch_robinhood_onchain_price(monitor, rpc_url)
            except Exception as exc:
                errors.append(f"{urllib.parse.urlparse(rpc_url).netloc}: {exc}")
    raise MonitorError("全部价格源失败：" + " | ".join(errors))


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"monitors": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"monitors": {}}
    if not isinstance(data, dict):
        return {"monitors": {}}
    if not isinstance(data.get("monitors"), dict):
        data["monitors"] = {}
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def build_price_message(monitor: dict[str, Any], market: dict[str, Any], status: str, previous: str | None) -> tuple[str, str]:
    labels = {"below": "跌破下限", "above": "突破上限", "inside": "回到区间"}
    title = f"{monitor['name']} 价格{labels[status]}"
    lines = [
        f"## {title}",
        f"- 当前价格：{fmt_decimal(market['price'])}{(' ' + monitor['price_unit']) if monitor.get('price_unit') else ''}",
        f"- 监控区间：{fmt_decimal(monitor['lower'])} ～ {fmt_decimal(monitor['upper'])}{(' ' + monitor['price_unit']) if monitor.get('price_unit') else ''}",
        f"- 数据源：{market.get('source') or market['dex']} · {market['base_symbol']}/{market['quote_symbol']} · {monitor['chain']}",
    ]
    if previous and previous != status:
        lines.append(f"- 状态变化：{previous} → {status}")
    if market.get("liquidity_usd") is not None:
        try:
            lines.append(f"- 流动性：${float(market['liquidity_usd']):,.2f}")
        except (TypeError, ValueError):
            pass
    if market.get("url"):
        lines.append(f"- [打开 DexScreener]({market['url']})")
    lines.append(f"- 时间：{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    return title, "\n".join(lines)


def send_wxpusher(wx: dict[str, Any], title: str, content: str) -> None:
    payload = {
        "appToken": wx["app_token"],
        "content": content,
        "summary": title[:100],
        "contentType": 3,
        "uids": wx["uids"],
        "topicIds": wx["topic_ids"],
    }
    data = request_json(WXPUSHER_API, method="POST", payload=payload)
    if not isinstance(data, dict) or data.get("code") != 1000:
        message = data.get("msg") if isinstance(data, dict) else "未知错误"
        raise MonitorError(f"WxPusher 推送失败：{message}")


def should_alert(previous: str | None, current: str, last_alert_ts: float, now: float, cooldown: int) -> bool:
    if current == "inside":
        return previous in {"below", "above"}
    if previous != current:
        return True
    return cooldown > 0 and now - last_alert_ts >= cooldown


def position_is_active(item: dict[str, Any], wallet: str) -> bool:
    """Only called immediately before a real alert; unknown RPC state blocks delivery."""
    if item.get("chain") != "robinhood" or item.get("position_id") is None or not wallet:
        return True
    tid = encode_uint256(item["position_id"])
    last = None
    for url in ROBINHOOD_RPC_URLS[:2]:
        try:
            raw = eth_call_rpc(url, ROBINHOOD_POSITION_MANAGER, "0x" + function_selector("getPositionLiquidity(uint256)") + tid)
            if int(raw, 16) == 0:
                return False
            owner = eth_call_rpc(url, ROBINHOOD_POSITION_MANAGER, "0x" + function_selector("ownerOf(uint256)") + tid)
            if len(owner) != 66:
                raise MonitorError("所有权数据长度异常")
            return "0x" + owner[-40:].lower() == wallet
        except Exception as exc:
            last = exc
    raise MonitorError(f"通知前无法确认 LP 仍有效，本次不推送：{last}")


def run_cycle(
    config: dict[str, Any],
    wx: dict[str, Any] | None,
    dry_run: bool,
    verbose: bool,
    persist_state: bool = True,
) -> int:
    state = load_state(config["state_path"])
    now = time.time()
    failures = 0

    for monitor in config["monitors"]:
        if not monitor["enabled"]:
            continue
        saved = state["monitors"].setdefault(monitor["id"], {})
        try:
            market = fetch_pair_price(monitor)
            current = classify_price(market["price"], monitor["lower"], monitor["upper"])
            previous = saved.get("status")
            last_alert_ts = float(saved.get("last_alert_ts", 0) or 0)
            previous_lower = str(saved.get("lower", "") or "")
            previous_upper = str(saved.get("upper", "") or "")
            current_lower = fmt_decimal(monitor["lower"])
            current_upper = fmt_decimal(monitor["upper"])
            bounds_changed = bool(previous_lower or previous_upper) and (
                previous_lower != current_lower or previous_upper != current_upper
            )
            alert = should_alert(previous, current, last_alert_ts, now, config["repeat_alert_seconds"])
            # LP 重建后区间会改变。如果新配置加载时价格已经在区间外，不能沿用旧区间的
            # below/above 状态压掉提醒；此时按“新状态”立即通知一次。
            if bounds_changed and current in {"below", "above"}:
                alert = True

            if verbose:
                print(f"{monitor['name']}: {fmt_decimal(market['price'])} [{current}]")

            if alert and not dry_run and not position_is_active(monitor, config.get("wallet", "")):
                saved.update(status="inactive", last_checked_ts=now, last_error="", suppressed_reason="LP已退出或转出")
                continue
            if alert:
                title, content = build_price_message(monitor, market, current, previous)
                if dry_run:
                    print(f"\n[模拟提醒] {title}\n{content}\n")
                else:
                    if wx is None:
                        raise MonitorError("内部错误：未加载 WxPusher 配置")
                    send_wxpusher(wx, title, content)
                saved["last_alert_ts"] = now

            saved.update(
                {
                    "status": current,
                    "last_price": fmt_decimal(market["price"]),
                    "lower": current_lower,
                    "upper": current_upper,
                    "last_checked_ts": now,
                    "last_error": "",
                }
            )
        except Exception as exc:
            failures += 1
            saved["last_checked_ts"] = now
            saved["last_error"] = str(exc)
            if verbose:
                print(f"{monitor['name']}: 检查失败：{exc}", file=sys.stderr)

    # 模拟检查不能污染正式监控状态，否则一次 dry-run 会让正式进程误以为
    # 已经提醒过，从而在冷却期内漏掉真实通知。
    if persist_state:
        save_state(config["state_path"], state)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Meme 币价格区间监控")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="监控配置 JSON")
    parser.add_argument("--wx-config", default=str(DEFAULT_WX_CONFIG_PATH), help="WxPusher 配置 JSON")
    parser.add_argument("--once", action="store_true", help="只检查一轮")
    parser.add_argument("--dry-run", action="store_true", help="不发送 WxPusher，只打印本次提醒")
    parser.add_argument("--verbose", action="store_true", help="打印价格与错误")
    parser.add_argument("--validate", action="store_true", help="只校验配置，不请求网络")
    args = parser.parse_args()

    try:
        config_path = Path(args.config).expanduser().resolve()
        config = normalize_config(load_json(config_path), config_path)
        if args.validate:
            print(f"配置有效，共 {len(config['monitors'])} 个监控项")
            return 0
        wx = None if args.dry_run else load_wxpusher(Path(args.wx_config).expanduser().resolve())
        while True:
            # 每轮重新读取配置，确保运行中的 PM2 进程能自动发现新增、删除
            # 或修改的监控项，不再依赖人工重启。
            config = normalize_config(load_json(config_path), config_path)
            failures = run_cycle(
                config,
                wx,
                args.dry_run,
                args.verbose,
                persist_state=not args.dry_run,
            )
            if args.once:
                return 1 if failures else 0
            time.sleep(config["poll_interval_seconds"])
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
