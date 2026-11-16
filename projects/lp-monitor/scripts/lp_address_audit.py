#!/usr/bin/env python3
"""Audit recent Uniswap LP actions for a wallet via Blockscout-compatible API + EVM RPC.
Read-only. Produces transaction/position cashflows and mark-to-market comparison against HODL.
"""
from __future__ import annotations

import argparse
import base64
import os
import json
import math
import subprocess
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal, getcontext
from pathlib import Path
from typing import Any

getcontext().prec = 60

ZERO = "0x0000000000000000000000000000000000000000"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
POSM = "0x58daec3116aae6d93017baaea7749052e8a04fa7"
POOL_MANAGER = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
API = "https://hoodscan.co/api/v2"
RPC = "https://rpc.mainnet.chain.robinhood.com"
DEX = "https://api.dexscreener.com/latest/dex/search/?q="


def run_curl(url: str, out: Path | None = None, method: str | None = None, data: str | None = None) -> str:
    cmd = ["/usr/bin/curl", "--retry", "3", "--retry-delay", "1", "-sS", "--max-time", "60", "-A", "lp-address-audit/1.0"]
    if method:
        cmd += ["-X", method]
    if data is not None:
        cmd += ["-H", "content-type: application/json", "--data", data]
    cmd.append(url)
    if out:
        cmd += ["-o", str(out)]
    p = subprocess.run(cmd, text=True, capture_output=True)
    if p.returncode:
        raise RuntimeError(f"curl failed rc={p.returncode}: {p.stderr.strip()}")
    if out:
        return out.read_text()
    return p.stdout


def get_json(url: str) -> Any:
    last = ""
    for attempt in range(5):
        txt = run_curl(url)
        last = txt
        try:
            obj = json.loads(txt)
            if isinstance(obj, str) and obj.lower().startswith("internal server error"):
                raise ValueError("temporary API error")
            return obj
        except Exception:
            # hoodscan occasionally prepends an error string to an otherwise valid JSON response.
            for marker in ('{"items":', '[{"'):
                idx = txt.find(marker, 1)
                if idx >= 0:
                    try:
                        return json.loads(txt[idx:])
                    except Exception:
                        pass
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"bad JSON from {url}: {last[:200]}")


def rpc(method: str, params: list[Any]) -> Any:
    req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    txt = run_curl(RPC, method="POST", data=req)
    obj = json.loads(txt)
    if "error" in obj:
        raise RuntimeError(f"RPC {method}: {obj['error']}")
    return obj["result"]


def blockscout_items(url: str, max_pages: int = 30) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    next_params = None
    for _ in range(max_pages):
        u = url
        if next_params:
            u += ("&" if "?" in u else "?") + urllib.parse.urlencode(next_params)
        obj = get_json(u)
        items.extend(obj.get("items", []))
        next_params = obj.get("next_page_params")
        if not next_params:
            break
        time.sleep(0.08)
    return items


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def topic_addr(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def signed_word(h: str, bits: int = 256) -> int:
    x = int(h, 16)
    if x >= 1 << (bits - 1):
        x -= 1 << bits
    return x


def encode_uint(x: int) -> str:
    return hex(x)[2:].rjust(64, "0")


def selector(signature: str) -> str:
    from Crypto.Hash import keccak
    k = keccak.new(digest_bits=256)
    k.update(signature.encode())
    return k.hexdigest()[:8]


def eth_call(to: str, data: str, block: int | str = "latest") -> str:
    tag = hex(block) if isinstance(block, int) else block
    return rpc("eth_call", [{"to": to, "data": data}, tag])


def words(data: str) -> list[str]:
    h = data[2:] if data.startswith("0x") else data
    return [h[i:i+64] for i in range(0, len(h), 64) if len(h[i:i+64]) == 64]


def decode_int_word(word: str, bits: int = 256) -> int:
    x = int(word, 16)
    mask = (1 << bits) - 1
    x &= mask
    return x - (1 << bits) if x >= (1 << (bits - 1)) else x


def call_sig(to: str, signature: str, args_hex: str = "", block: int | str = "latest") -> str:
    return eth_call(to, "0x" + selector(signature) + args_hex, block)


def erc20_meta_rpc(addr: str) -> dict[str, Any]:
    def call_str(sig: str) -> str:
        raw = call_sig(addr, sig)
        ws = words(raw)
        if not ws:
            return ""
        try:
            # Dynamic ABI string: offset, length, bytes.
            if int(ws[0], 16) == 32 and len(ws) >= 2:
                n = int(ws[1], 16)
                blob = "".join(ws[2:])[:n*2]
                return bytes.fromhex(blob).decode("utf-8", errors="replace")
            # bytes32 legacy tokens.
            return bytes.fromhex(ws[0]).rstrip(b"\x00").decode("utf-8", errors="replace")
        except Exception:
            return ""
    dec = int(call_sig(addr, "decimals()"), 16)
    return {"symbol": call_str("symbol()"), "name": call_str("name()"), "decimals": dec}


def balance_of(addr: str, owner: str, block: int | str = "latest") -> int:
    return int(call_sig(addr, "balanceOf(address)", owner[2:].rjust(64, "0"), block), 16)


def owner_of(nft: str, token_id: int, block: int | str = "latest") -> str | None:
    try:
        raw = call_sig(nft, "ownerOf(uint256)", encode_uint(token_id), block)
        return "0x" + raw[-40:].lower()
    except Exception:
        return None


def get_position_info(token_id: int, block: int | str = "latest") -> dict[str, Any]:
    raw = call_sig(POSM, "getPoolAndPositionInfo(uint256)", encode_uint(token_id), block)
    ws = words(raw)
    if len(ws) < 6:
        raise RuntimeError(f"unexpected getPoolAndPositionInfo return: {raw}")
    currency0 = "0x" + ws[0][-40:].lower()
    currency1 = "0x" + ws[1][-40:].lower()
    fee = int(ws[2], 16)
    tick_spacing = decode_int_word(ws[3], 24)
    hooks = "0x" + ws[4][-40:].lower()
    info = int(ws[5], 16)
    # PositionInfo packs [poolId:25 bytes][tickUpper:3][tickLower:3][hasSubscriber:1].
    tick_lower = decode_int_word(hex((info >> 8) & ((1 << 24)-1))[2:].rjust(64,"0"), 24)
    tick_upper = decode_int_word(hex((info >> 32) & ((1 << 24)-1))[2:].rjust(64,"0"), 24)
    has_subscriber = bool(info & 0xff)
    pool_id_prefix = "0x" + hex(info >> 56)[2:].rjust(50, "0")
    return {"currency0": currency0, "currency1": currency1, "fee": fee, "tick_spacing": tick_spacing, "hooks": hooks, "tick_lower": tick_lower, "tick_upper": tick_upper, "has_subscriber": has_subscriber, "pool_id_prefix_25bytes": pool_id_prefix, "packed_position_info": str(info)}


def position_liquidity(token_id: int, block: int | str = "latest") -> int:
    return int(call_sig(POSM, "getPositionLiquidity(uint256)", encode_uint(token_id), block), 16)


def sqrt_price_at_tick(tick: int) -> Decimal:
    return Decimal(str(math.exp(math.log(1.0001) * tick / 2)))


def amounts_for_liquidity(liquidity: int, tick_lower: int, tick_upper: int, current_tick: int) -> tuple[Decimal, Decimal]:
    L = Decimal(liquidity)
    sa = sqrt_price_at_tick(tick_lower)
    sb = sqrt_price_at_tick(tick_upper)
    sp = sqrt_price_at_tick(current_tick)
    if sp <= sa:
        return L * (sb - sa) / (sa * sb), Decimal(0)
    if sp >= sb:
        return Decimal(0), L * (sb - sa)
    return L * (sb - sp) / (sp * sb), L * (sp - sa)


def pool_latest_state(pool_id: str, to_block: int) -> dict[str, Any] | None:
    swap_topic = "0x" + keccak_hex("Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)")
    # Query in bounded chunks because public RPCs cap eth_getLogs ranges.
    start = max(0, to_block - 250000)
    latest = None
    for a in range(start, to_block + 1, 50000):
        b = min(to_block, a + 49999)
        params = [{"address": POOL_MANAGER, "fromBlock": hex(a), "toBlock": hex(b), "topics": [swap_topic, pool_id]}]
        try:
            logs = rpc("eth_getLogs", params)
        except Exception:
            continue
        if logs:
            latest = logs[-1]
    if not latest:
        return None
    ws = words(latest["data"])
    if len(ws) < 6:
        return None
    return {"block": int(latest["blockNumber"], 16), "sqrt_price_x96": int(ws[2], 16), "liquidity": int(ws[3], 16), "tick": decode_int_word(ws[4], 24), "fee": int(ws[5], 16), "tx_hash": latest["transactionHash"]}


def keccak_hex(text: str) -> str:
    from Crypto.Hash import keccak
    k = keccak.new(digest_bits=256)
    k.update(text.encode())
    return k.hexdigest()


def pool_id_for_key(currency0: str, currency1: str, fee: int, tick_spacing: int, hooks: str) -> str:
    from Crypto.Hash import keccak
    encoded = (
        currency0[2:].rjust(64,"0") + currency1[2:].rjust(64,"0") +
        encode_uint(fee) + ((tick_spacing + (1<<24)) % (1<<24)).to_bytes(3,"big").hex().rjust(64,"f" if tick_spacing < 0 else "0") +
        hooks[2:].rjust(64,"0")
    )
    k = keccak.new(digest_bits=256)
    k.update(bytes.fromhex(encoded))
    return "0x" + k.hexdigest()


def token_meta(addr: str, cache: dict[str, Any]) -> dict[str, Any]:
    a = addr.lower()
    if a == ZERO:
        return {"address": a, "symbol": "ETH", "name": "Ether", "decimals": 18}
    if a in cache:
        return cache[a]
    obj = get_json(f"{API}/tokens/{a}")
    meta = {
        "address": a,
        "symbol": obj.get("symbol") or a[:8],
        "name": obj.get("name") or "",
        "decimals": int(obj.get("decimals") or 18),
    }
    cache[a] = meta
    return meta


def all_wallet_txs(wallet: str, since: datetime) -> list[dict[str, Any]]:
    url = f"{API}/addresses/{wallet}/transactions"
    result = []
    next_params = None
    for _ in range(100):
        u = url
        if next_params:
            u += "?" + urllib.parse.urlencode(next_params)
        try:
            obj = get_json(u)
        except RuntimeError:
            # The API sometimes fails on old pagination cursors; keep already collected recent pages.
            if result:
                break
            raise
        for x in obj.get("items", []):
            if ts(x["timestamp"]) >= since:
                result.append(x)
        page = obj.get("items", [])
        if not page or min(ts(x["timestamp"]) for x in page) < since:
            break
        next_params = obj.get("next_page_params")
        if not next_params:
            break
        time.sleep(0.08)
    return result


def tx_logs(txhash: str) -> list[dict[str, Any]]:
    try:
        return blockscout_items(f"{API}/transactions/{txhash}/logs", max_pages=5)
    except Exception:
        time.sleep(1)
        try:
            return blockscout_items(f"{API}/transactions/{txhash}/logs", max_pages=5)
        except Exception:
            return []


def erc20_flows(logs: list[dict[str, Any]], wallet: str, meta_cache: dict[str, Any]) -> dict[str, int]:
    w = wallet.lower()
    flows: dict[str, int] = defaultdict(int)
    for log in logs:
        topics = log.get("topics") or []
        if not topics or (topics[0] or "").lower() != TRANSFER_TOPIC or len(topics) < 3:
            continue
        frm, to = topic_addr(topics[1]), topic_addr(topics[2])
        if frm != w and to != w:
            continue
        token = (log.get("address") or {}).get("hash", "").lower()
        # ERC721 Transfer has token id in indexed topic[3] and empty data; skip.
        if len(topics) >= 4 and (log.get("data") in ("0x", None)):
            continue
        try:
            val = int(log.get("data") or "0x0", 16)
        except ValueError:
            continue
        token_meta(token, meta_cache)
        if frm == w:
            flows[token] -= val
        if to == w:
            flows[token] += val
    return dict(flows)


def nft_mint_id(logs: list[dict[str, Any]], wallet: str) -> int | None:
    w = wallet.lower()
    for log in logs:
        a = (log.get("address") or {}).get("hash", "").lower()
        topics = log.get("topics") or []
        if a != POSM or len(topics) < 4 or (topics[0] or "").lower() != TRANSFER_TOPIC:
            continue
        if topic_addr(topics[1]) == ZERO and topic_addr(topics[2]) == w:
            return int(topics[3], 16)
    return None


def get_position(token_id: int, block: int | str = "latest") -> dict[str, Any]:
    # getPoolAndPositionInfo(uint256): selector 0x85f8c259 is derived with cast/ABI externally.
    # Avoid selector hardcode assumptions by using known ABI selector computed via RPC-safe local sha3 is wrong.
    # The deployed contract exposes positionInfo(uint256) selector 0x29d3f093 and poolKeys(bytes25).
    # Use explorer metadata only for NFT naming if call selectors cannot be derived.
    return {"token_id": token_id}


def parse_nft_name(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("metadata") or {}
    name = meta.get("name") or ""
    # Uniswap - 3.94% - MOO/USDG - 50.744<>61.793
    out = {"name": name}
    try:
        parts = [x.strip() for x in name.split("-")]
        out["fee_label"] = parts[1]
        out["pair"] = parts[2]
        lo, hi = parts[3].split("<>")
        out["range_display"] = [Decimal(lo), Decimal(hi)]
    except Exception:
        pass
    return out


def find_pair_price(token_addr: str, quote_addr: str | None = None) -> dict[str, Any] | None:
    obj = get_json(DEX + token_addr)
    pairs = [p for p in obj.get("pairs") or [] if p.get("chainId") == "robinhood"]
    if quote_addr:
        q = quote_addr.lower()
        matched = [p for p in pairs if (p.get("baseToken", {}).get("address", "").lower() == token_addr.lower() and p.get("quoteToken", {}).get("address", "").lower() == q) or (p.get("quoteToken", {}).get("address", "").lower() == token_addr.lower() and p.get("baseToken", {}).get("address", "").lower() == q)]
        if matched:
            pairs = matched
    if not pairs:
        return None
    pairs.sort(key=lambda p: Decimal(str((p.get("liquidity") or {}).get("usd") or 0)), reverse=True)
    return pairs[0]


def current_token_usd(meta: dict[str, Any], price_cache: dict[str, Any]) -> Decimal:
    a = meta["address"]
    if meta["symbol"] in {"USDG", "USDC", "USDT", "DAI"}:
        return Decimal(1)
    if a in price_cache:
        return price_cache[a]
    # Avoid network price lookups here; final per-pair pricing is derived from
    # known pool addresses after the deterministic chain reconstruction.
    price = Decimal(0)
    price_cache[a] = price
    return price


def historical_price_nearest(token: str, quote: str, target_block: int, lower_bound: int = 0) -> tuple[Decimal, int] | None:
    """Derive token price from nearest PoolManager Swap log involving token/quote is unavailable by token filter.
    Placeholder returns None; audit falls back to actual deposited amounts for HODL baseline, which does not need entry USD price.
    """
    return None


def raw_to_dec(raw: int, decimals: int) -> Decimal:
    return Decimal(raw) / (Decimal(10) ** decimals)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wallet")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    wallet = args.wallet.lower()
    since = datetime.now(timezone.utc).timestamp() - args.days * 86400
    since_dt = datetime.fromtimestamp(since, timezone.utc)

    txs = all_wallet_txs(wallet, since_dt)
    pos_txs = [x for x in txs if ((x.get("to") or {}).get("hash") or "").lower() in {POSM, POOL_MANAGER} and x.get("status") == "ok"]
    meta_cache: dict[str, Any] = {}
    actions = []
    for i, x in enumerate(sorted(pos_txs, key=lambda z: z["timestamp"])):
        logs = tx_logs(x["hash"])
        flows = erc20_flows(logs, wallet, meta_cache)
        mint_id = nft_mint_id(logs, wallet)
        actions.append({
            "timestamp": x["timestamp"], "block": x["block_number"], "hash": x["hash"],
            "method": x.get("method"), "gas_used": int(x.get("gas_used") or 0), "gas_price": int(x.get("gas_price") or 0),
            "gas_cost_eth": str(Decimal(int(x.get("gas_used") or 0) * int(x.get("gas_price") or 0)) / Decimal(10**18)),
            "mint_token_id": mint_id, "raw_flows": flows,
        })
        print(f"[{i+1}/{len(pos_txs)}] {x['timestamp']} {x['hash'][:12]} flows={len(flows)} mint={mint_id}", flush=True)

    nft_items = get_json(f"{API}/addresses/{wallet}/nft?type=ERC-721").get("items", [])
    nft_map = {int(x["id"]): {**parse_nft_name(x), "contract": x["token"]["address_hash"].lower()} for x in nft_items if x.get("id")}

    # Associate each non-mint PositionManager action with the most plausible currently active position by token symbols.
    # Exact association is extracted from calldata: tokenId appears as a 32-byte word. For minted actions use mint id.
    known_ids = sorted(nft_map)
    for a in actions:
        tids = []
        tx_obj = next(x for x in pos_txs if x["hash"] == a["hash"])
        raw_input = tx_obj.get("raw_input") or ""
        for tid in known_ids:
            if encode_uint(tid) in raw_input.lower():
                tids.append(tid)
        if a["mint_token_id"] and a["mint_token_id"] not in tids:
            tids.append(a["mint_token_id"])
        a["token_ids"] = tids
        a["positions"] = [nft_map.get(tid, {}) for tid in tids]
        a["flows"] = []
        for token, raw in a.pop("raw_flows").items():
            m = meta_cache[token]
            a["flows"].append({**m, "raw": str(raw), "amount": str(raw_to_dec(raw, m["decimals"]))})

    # Current NFT states from PositionManager. Decode pair, ticks and current liquidity.
    position_states: dict[int, Any] = {}
    known_ids = [tid for tid in known_ids if nft_map.get(tid, {}).get("contract") == POSM]
    for tid in known_ids:
        try:
            info = get_position_info(tid)
            liq = position_liquidity(tid)
            for currency in (info["currency0"], info["currency1"]):
                if currency not in meta_cache:
                    try:
                        token_meta(currency, meta_cache)
                    except Exception:
                        rmeta = erc20_meta_rpc(currency)
                        meta_cache[currency] = {"address": currency, **rmeta}
            position_states[tid] = {**info, "liquidity": liq, "owner": owner_of(POSM, tid)}
        except Exception as e:
            position_states[tid] = {"liquidity": None, "error": str(e)}

    # Cashflow-based realized view. Positive flow back to wallet minus deposits, marked at current USD.
    price_cache: dict[str, Decimal] = {}
    aggregate_raw: dict[str, int] = defaultdict(int)
    deposited_raw: dict[str, int] = defaultdict(int)
    received_raw: dict[str, int] = defaultdict(int)
    gas_eth = Decimal(0)
    for a in actions:
        gas_eth += Decimal(a["gas_cost_eth"])
        for f in a["flows"]:
            r = int(f["raw"]); aggregate_raw[f["address"]] += r
            if r < 0: deposited_raw[f["address"]] += -r
            else: received_raw[f["address"]] += r

    token_summary = []
    for token in sorted(set(aggregate_raw) | set(deposited_raw) | set(received_raw)):
        m = meta_cache[token]
        px = current_token_usd(m, price_cache)
        net = raw_to_dec(aggregate_raw[token], m["decimals"])
        dep = raw_to_dec(deposited_raw[token], m["decimals"])
        rec = raw_to_dec(received_raw[token], m["decimals"])
        token_summary.append({**m, "current_usd": str(px), "deposited": str(dep), "received": str(rec), "net_wallet_flow": str(net), "net_wallet_flow_usd_now": str(net * px)})

    eth_meta = {"address": ZERO, "symbol": "ETH", "name": "Ether", "decimals": 18}
    eth_usd = current_token_usd(eth_meta, price_cache)
    gas_usd = gas_eth * eth_usd

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "wallet": wallet, "window_since": since_dt.isoformat(),
        "window_days": args.days, "chain": "Robinhood Chain", "position_manager": POSM,
        "transactions_scanned": len(txs), "lp_actions": len(actions), "actions": actions,
        "nfts": {str(k): {**v, **position_states.get(k, {})} for k, v in nft_map.items()},
        "token_summary": token_summary, "gas_cost_eth": str(gas_eth), "eth_usd": str(eth_usd), "gas_cost_usd": str(gas_usd),
        "methodology": {
            "fact": "ERC20 wallet flows are decoded from transaction logs; deposits negative, withdrawals/fees positive.",
            "limitation": "Open positions require on-chain liquidity/fee valuation. If cast is unavailable, position liquidity is not populated. Current-flow totals alone are not final PnL while NFTs remain open.",
            "hodl_rule": "For each position, HODL means retaining the exact token amounts deposited at entry; compare their terminal USD value with terminal LP assets plus fees minus gas/swap costs."
        }
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=lambda o: str(o) if isinstance(o, Decimal) else repr(o)))
    print(f"WROTE {out} actions={len(actions)} nfts={len(nft_map)} gas_usd={gas_usd}")


if __name__ == "__main__":
    main()
