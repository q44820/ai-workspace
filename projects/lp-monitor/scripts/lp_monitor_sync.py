#!/usr/bin/env python3
"""同步 Robinhood Chain 钱包当前有效 Uniswap v4 LP 到价格监控配置。

只读链上数据；默认 preview，不改配置。使用 --apply 才会写入配置。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.request
from decimal import Decimal, getcontext
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import lp_address_audit as chain  # noqa: E402
import meme_lp_price_monitor as monitor  # noqa: E402

getcontext().prec = 60
DEFAULT_CONFIG = SCRIPT_DIR / "meme_lp_price_monitor.json"
DEX_SEARCH_API = "https://api.dexscreener.com/latest/dex/search/?q="


class SyncError(RuntimeError):
    pass


def request_json(url: str, timeout: int = 25) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "lp-monitor-sync/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as first_exc:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise SyncError(f"网络请求失败：{exc}") from first_exc


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncError(f"无法读取配置：{path}：{exc}") from exc
    if not isinstance(data, dict):
        raise SyncError("配置顶层必须是对象")
    return data


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def nft_positions(wallet: str) -> list[dict[str, Any]]:
    url = f"{chain.API}/addresses/{wallet.lower()}/nft?type=ERC-721"
    data = request_json(url)
    if not isinstance(data, dict):
        raise SyncError("Hoodscan NFT 接口返回异常")
    result: list[dict[str, Any]] = []
    for item in data.get("items") or []:
        token = item.get("token") or {}
        if str(token.get("address_hash") or "").lower() != chain.POSM:
            continue
        try:
            token_id = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        result.append({
            "position_id": token_id,
            "metadata": chain.parse_nft_name(item),
        })
    return result


def token_meta(address: str) -> dict[str, Any]:
    if address.lower() == chain.ZERO:
        return {"address": chain.ZERO, "symbol": "ETH", "decimals": 18}
    try:
        data = request_json(f"{chain.API}/tokens/{address.lower()}")
        symbol = str(data.get("symbol") or "").strip()
        decimals = int(data.get("decimals"))
        if symbol:
            return {"address": address.lower(), "symbol": symbol, "decimals": decimals}
    except Exception:
        pass
    try:
        data = chain.erc20_meta_rpc(address)
        return {"address": address.lower(), "symbol": str(data.get("symbol") or address[:8]), "decimals": int(data["decimals"])}
    except Exception as exc:
        raise SyncError(f"读取代币信息失败：{address}：{exc}") from exc


def price_token_per_quote_at_tick(tick: int, token0: dict[str, Any], token1: dict[str, Any]) -> Decimal:
    """返回 token1/token0 的人类可读价格。"""
    raw_token1_per_token0 = Decimal("1.0001") ** Decimal(tick)
    return raw_token1_per_token0 * (Decimal(10) ** (token0["decimals"] - token1["decimals"]))


def u_range(info: dict[str, Any], token0: dict[str, Any], token1: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Decimal, Decimal]:
    stable = {"USDG", "USDC", "USDT", "DAI"}
    if token0["symbol"] in stable and token1["symbol"] not in stable:
        quote, token = token0, token1
        values = []
        for tick in (info["tick_lower"], info["tick_upper"]):
            token1_per_token0 = price_token_per_quote_at_tick(tick, token0, token1)
            values.append(Decimal(1) / token1_per_token0)
    elif token1["symbol"] in stable and token0["symbol"] not in stable:
        token, quote = token0, token1
        values = [price_token_per_quote_at_tick(tick, token0, token1) for tick in (info["tick_lower"], info["tick_upper"])]
    else:
        raise SyncError(f"仅支持稳定币报价 LP，实际为 {token0['symbol']}/{token1['symbol']}")
    return token, quote, min(values), max(values)


def select_pair(token: dict[str, Any], quote: dict[str, Any]) -> dict[str, Any]:
    data = request_json(DEX_SEARCH_API + token["address"])
    pairs = data.get("pairs") if isinstance(data, dict) else None
    candidates = []
    for pair in pairs or []:
        if str(pair.get("chainId") or "").lower() != "robinhood":
            continue
        if str(pair.get("dexId") or "").lower() != "uniswap":
            continue
        labels = [str(value).lower() for value in (pair.get("labels") or [])]
        if "v4" not in labels:
            continue
        base = pair.get("baseToken") or {}
        q = pair.get("quoteToken") or {}
        addresses = {str(base.get("address") or "").lower(), str(q.get("address") or "").lower()}
        if {token["address"], quote["address"]} != addresses:
            continue
        liquidity = Decimal(str((pair.get("liquidity") or {}).get("usd") or 0))
        candidates.append((liquidity, pair))
    if not candidates:
        raise SyncError(f"DexScreener 未找到 {token['symbol']}/{quote['symbol']} 的 Robinhood Uniswap v4 池")
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def monitor_item(position_id: int, info: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    token0 = token_meta(info["currency0"])
    token1 = token_meta(info["currency1"])
    token, quote, lower, upper = u_range(info, token0, token1)
    pair = select_pair(token, quote)
    base = pair.get("baseToken") or {}
    q = pair.get("quoteToken") or {}
    base_addr = str(base.get("address") or "").lower()
    quote_addr = str(q.get("address") or "").lower()
    if base_addr == token["address"] and quote_addr == quote["address"]:
        invert_price = False
    elif base_addr == quote["address"] and quote_addr == token["address"]:
        invert_price = True
    else:
        raise SyncError("DexScreener 返回的交易对地址不匹配")
    fee_label = str(metadata.get("fee_label") or f"{Decimal(info['fee']) / Decimal(10000)}%")
    return {
        "name": f"{token['symbol']} U价区间",
        "chain": "robinhood",
        "token_address": token["address"],
        "pair_address": str(pair.get("pairAddress") or ""),
        "lower": monitor.fmt_decimal(lower),
        "upper": monitor.fmt_decimal(upper),
        "price_field": "priceNative",
        "invert_price": invert_price,
        "price_unit": f"U/{token['symbol']}",
        "expected_base_symbol": str(base.get("symbol") or ""),
        "expected_quote_symbol": str(q.get("symbol") or ""),
        "expected_dex": "uniswap",
        "expected_label": "v4",
        "enabled": True,
        "position_id": position_id,
        "tick_lower": info["tick_lower"],
        "tick_upper": info["tick_upper"],
        "fee_tier": fee_label,
        "note": "钱包当前有效LP；区间由Uniswap v4链上仓位自动同步",
    }


def build_config(wallet: str, current: dict[str, Any], keep_disabled: bool = False) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    active: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for position in nft_positions(wallet):
        token_id = position["position_id"]
        try:
            liquidity = chain.position_liquidity(token_id)
            if liquidity <= 0:
                continue
            info = chain.get_position_info(token_id)
            active.append(monitor_item(token_id, info, position["metadata"]))
        except Exception as exc:
            errors.append({"position_id": token_id, "error": str(exc)})
    if not active:
        raise SyncError("没有找到可同步的有效 LP")
    active.sort(key=lambda item: item["position_id"], reverse=True)
    active_ids = {item["position_id"] for item in active}
    stale: list[dict[str, Any]] = []
    for old in current.get("monitors") or []:
        old_position = old.get("position_id")
        if old_position in active_ids:
            continue
        item = dict(old)
        item["enabled"] = False
        item["note"] = "链上流动性为0或仓位已失效；自动停用"
        stale.append(item)
    updated = dict(current)
    updated["monitors"] = active + (stale if keep_disabled else [])
    return updated, active, stale


def summary(active: list[dict[str, Any]], stale: list[dict[str, Any]], applied: bool) -> None:
    print("已写入配置" if applied else "预览完成，未修改配置")
    for item in active:
        print(f"有效 #{item['position_id']} {item['name'].replace(' U价区间','')}：{item['lower']}～{item['upper']} {item['price_unit']}")
    if stale:
        names = [f"#{item.get('position_id', '?')} {item.get('name', '未知')}" for item in stale]
        print("已识别失效/旧监控：" + "、".join(names))


def main() -> int:
    parser = argparse.ArgumentParser(description="同步钱包当前有效 LP 到价格区间监控")
    parser.add_argument("--wallet", help="钱包地址；默认读取配置中的 wallet 字段或 LP_MONITOR_WALLET")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="监控配置 JSON")
    parser.add_argument("--apply", action="store_true", help="实际写入配置；默认仅预览")
    parser.add_argument("--keep-disabled", action="store_true", help="保留失效监控项但自动设为 disabled")
    parser.add_argument("--verify", action="store_true", help="写入后执行配置校验和联网 dry-run")
    args = parser.parse_args()
    config_path = Path(args.config).expanduser().resolve()
    try:
        current = load_json(config_path)
        wallet = str(args.wallet or current.get("wallet") or os.getenv("LP_MONITOR_WALLET") or "").strip().lower()
        if not wallet.startswith("0x") or len(wallet) != 42:
            raise SyncError("缺少有效钱包地址，请使用 --wallet 或在配置中设置 wallet")
        updated, active, stale = build_config(wallet, current, args.keep_disabled)
        updated["wallet"] = wallet
        if args.apply:
            write_json_atomic(config_path, updated)
            if args.verify:
                normalized = monitor.normalize_config(updated, config_path)
                failures = monitor.run_cycle(normalized, None, True, True, persist_state=False)
                if failures:
                    raise SyncError(f"联网验证失败：{failures} 个监控项检查失败")
        summary(active, stale, args.apply)
        return 0
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
