#!/usr/bin/env python3
"""Read-only wallet reconciliation; no signing, transactions or notification spam.

A separate low-frequency worker discovers POSM NFTs and updates the existing
price monitor atomically. Unknown RPC/API results never mean a closed position.
"""
from __future__ import annotations
import argparse
import copy
import fcntl
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import lp_monitor_sync as sync
import lp_address_audit as chain
import meme_lp_price_monitor as monitor

TOKEN_METADATA = {}


class RPC:
    def __init__(self):
        self.url = None

    def __call__(self, method, params):
        urls = list(monitor.ROBINHOOD_RPC_URLS[:2])
        if self.url in urls:
            urls.remove(self.url)
            urls.insert(0, self.url)
        last = None
        for url in urls:
            try:
                result = monitor.rpc_call(url, method, params)
                self.url = url
                return result
            except Exception as exc:
                last = exc
        raise sync.SyncError(f"链上查询失败：{last}")


def transfer_ids(rpc, wallet, start, end):
    if start > end:
        return set()
    result = set()
    # Public RPC accepts a 1M-block range; split large catch-up scans explicitly.
    for lower in range(start, end + 1, 1000000):
        upper = min(end, lower + 999999)
        logs = rpc('eth_getLogs', [{'address': chain.POSM,
            'fromBlock': hex(lower), 'toBlock': hex(upper),
            'topics': [chain.TRANSFER_TOPIC, None, '0x' + wallet[2:].rjust(64, '0')]}])
        if not isinstance(logs, list):
            raise sync.SyncError('NFT 转入日志格式异常')
        for log in logs:
            if log.get('removed') or len(log.get('topics', [])) != 4:
                raise sync.SyncError('NFT 转入日志不完整或发生重组')
            result.add(int(log['topics'][3], 16))
    return result


def explorer_ids(wallet):
    base = f'{chain.API}/addresses/{wallet}/nft?type=ERC-721'
    url, result, seen = base, set(), set()
    for _ in range(100):
        data = sync.request_json(url, timeout=8)
        if not isinstance(data, dict) or not isinstance(data.get('items'), list):
            raise sync.SyncError('NFT 索引返回不完整')
        for item in data['items']:
            if str((item.get('token') or {}).get('address_hash', '')).lower() == chain.POSM:
                result.add(int(item['id']))
        page = data.get('next_page_params')
        if not page:
            return result
        key = urlencode(page)
        if key in seen:
            raise sync.SyncError('NFT 索引分页循环')
        seen.add(key)
        url = base + '&' + key
    raise sync.SyncError('NFT 索引分页超过安全上限')


def owner_at(token_id, block):
    try:
        raw = chain.call_sig(chain.POSM, 'ownerOf(uint256)', chain.encode_uint(token_id), block)
        if len(raw) != 66:
            raise sync.SyncError('NFT 所有者返回长度异常')
        return '0x' + raw[-40:].lower()
    except Exception as exc:
        # Only an explicit ERC721NonexistentToken revert is evidence of a burn.
        text = str(exc).lower()
        if '7e273289' in text or 'erc721nonexistenttoken' in text or 'invalid token id' in text:
            return None
        raise


def current_ids(wallet, current, state, rpc, block):
    candidates = {int(m['position_id']) for m in current.get('monitors', []) if m.get('position_id') is not None}
    if state.get('wallet') == wallet:
        candidates.update(state.get('owned_ids', []))
        cursor = int(state.get('last_discovery_block') or 0)
    else:
        cursor = 0
    start = max(0, cursor - 128) if cursor else max(0, block - 999999)
    candidates.update(transfer_ids(rpc, wallet, start, block))
    balance = chain.balance_of(chain.POSM, wallet, block)
    owners = {}

    def owned():
        for tid in sorted(candidates - owners.keys()):
            owners[tid] = owner_at(tid, block)
        return {tid for tid, owner in owners.items() if owner == wallet}

    ids = owned()
    if len(ids) != balance:
        # Missing old NFTs/indexer lag must not silently truncate the wallet.
        try:
            candidates.update(explorer_ids(wallet))
            ids = owned()
        except Exception:
            pass
    scanned = 0
    while len(ids) < balance and start > 0 and scanned < 10:
        end = start - 1
        start = max(0, end - 999999)
        candidates.update(transfer_ids(rpc, wallet, start, end))
        ids = owned()
        scanned += 1
    if len(ids) != balance:
        raise sync.SyncError(f'NFT 完整性校验失败：发现 {len(ids)}，链上余额 {balance}；保留旧配置')
    return ids


def build_item(tid, info, old):
    # Cache metadata for this process; use public RPC rather than an indexer.
    def meta(address):
        if address not in TOKEN_METADATA:
            data = chain.erc20_meta_rpc(address) if address != chain.ZERO else {'symbol': 'ETH', 'decimals': 18}
            TOKEN_METADATA[address] = dict(data, address=address)
        return TOKEN_METADATA[address]
    token0, token1 = meta(info['currency0']), meta(info['currency1'])
    token, quote, lower, upper = sync.u_range(info, token0, token1)
    if old and old.get('token_address') == token['address']:
        item = copy.deepcopy(old)
        item.update(lower=monitor.fmt_decimal(lower), upper=monitor.fmt_decimal(upper),
                    tick_lower=info['tick_lower'], tick_upper=info['tick_upper'], enabled=True)
    else:
        original = sync.token_meta
        try:
            sync.token_meta = meta
            item = sync.monitor_item(tid, info, {})
        finally:
            sync.token_meta = original
    item['id'] = f'robinhood:position:{tid}'
    item['note'] = '链上所有权及非零流动性核验；低频自动同步区间'
    return item


def reconcile(path, state_path, apply=True):
    current = sync.load_json(path)
    wallet = str(current.get('wallet', '')).lower()
    if len(wallet) != 42 or not wallet.startswith('0x'):
        raise sync.SyncError('未配置有效钱包')
    state = sync.load_json(state_path) if state_path.exists() else {}
    rpc = RPC()
    chain.rpc = rpc
    block = max(0, int(rpc('eth_blockNumber', []), 16) - 6)
    old_items = {int(m['position_id']): m for m in current.get('monitors', []) if m.get('position_id') is not None}
    errors, checked, active, removed = [], [], [], []
    try:
        ids = current_ids(wallet, current, state, rpc, block)
        discovery_ok = True
    except Exception as exc:
        # Still retire individually verified withdrawals if discovery is down.
        ids = set(old_items)
        discovery_ok = False
        errors.append({'stage': 'discovery', 'error': str(exc)})
    for tid in sorted(ids | old_items.keys(), reverse=True):
        old = old_items.get(tid)
        try:
            liquidity = chain.position_liquidity(tid, block)
            owner = owner_at(tid, block) if liquidity > 0 else None
            valid = liquidity > 0 and owner == wallet
            checked.append({'position_id': tid, 'liquidity': str(liquidity), 'active': valid})
            if not valid:
                if old:
                    removed.append(tid)
                continue
            info = chain.get_position_info(tid, block)
            item = build_item(tid, info, old)
            # New/range-changed entries are only committed after live pricing works.
            if not old or any(item.get(k) != old.get(k) for k in ('lower', 'upper', 'pair_address')):
                normalized = monitor.normalize_config(dict(current, monitors=[item]), path)
                monitor.fetch_pair_price(normalized['monitors'][0])
            active.append(item)
        except Exception as exc:
            errors.append({'position_id': tid, 'error': str(exc)})
            if old:
                active.append(old)
    # Preserve explicitly configured non-POSM price monitors.
    active += [m for m in current.get('monitors', []) if m.get('position_id') is None]
    updated = dict(current, monitors=active)
    monitor.normalize_config(updated, path)
    changed = current.get('monitors') != active
    if apply and changed:
        # Refuse to clobber a concurrent human/other writer's update.
        if sync.load_json(path) != current:
            raise sync.SyncError('同步期间配置已被其他进程修改；本次不覆盖')
        backup = path.parent / 'backups' / f'{path.stem}-auto-{time.time_ns()}.json'
        sync.write_json_atomic(backup, current)
        sync.write_json_atomic(path, updated)
    report = dict(state, wallet=wallet, last_attempt_ts=time.time(), snapshot_block=block,
        changed=changed, discovery_ok=discovery_ok, checks=checked,
        active_ids=[m.get('position_id') for m in active], removed_ids=removed, errors=errors)
    if discovery_ok:
        report.update(owned_ids=sorted(ids), last_discovery_block=block)
    if not errors:
        report['last_success_ts'] = time.time()
    if apply:
        sync.write_json_atomic(state_path, report)
    return report


def main():
    parser = argparse.ArgumentParser(description='低频自动同步钱包 LP 监控')
    parser.add_argument('--config', default=str(sync.DEFAULT_CONFIG))
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--preview', action='store_true')
    args = parser.parse_args()
    path = Path(args.config).resolve()
    state_path = path.with_name('lp_wallet_watchdog_state.json')
    with path.with_name('lp_wallet_watchdog.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('已有同步进程，本次退出', flush=True)
            return 0
        while True:
            current = sync.load_json(path)
            setting = current.get('wallet_sync', {})
            interval = max(300, int(setting.get('interval_seconds', 1800)))
            state = sync.load_json(state_path) if state_path.exists() else {}
            due = time.time() - float(state.get('last_attempt_ts', 0)) >= interval
            if args.once or (setting.get('enabled', True) and due):
                try:
                    report = reconcile(path, state_path, apply=not args.preview)
                    print(json.dumps({k: report[k] for k in ['snapshot_block', 'changed', 'discovery_ok', 'active_ids', 'removed_ids', 'errors']}, ensure_ascii=False), flush=True)
                    if args.once:
                        return 1 if report['errors'] else 0
                except Exception as exc:
                    state.update(last_attempt_ts=time.time(), errors=[{'stage': 'cycle', 'error': str(exc)}])
                    if not args.preview:
                        sync.write_json_atomic(state_path, state)
                    print('同步失败，保留原监控：' + str(exc), flush=True)
                    if args.once:
                        return 1
            if args.preview:
                return 0
            time.sleep(30)


if __name__ == '__main__':
    raise SystemExit(main())
