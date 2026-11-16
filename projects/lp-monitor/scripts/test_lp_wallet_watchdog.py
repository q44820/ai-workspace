#!/usr/bin/env python3
"""Regression tests use explicit mocks, never presented as live chain evidence."""
import copy
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
import lp_wallet_watchdog as w
import meme_lp_price_monitor as m

WALLET = '0x' + 'ab' * 20
ITEM = dict(name='TEST', chain='robinhood', pair_address='0xpool', token_address='0xtoken',
            position_id=1, lower='1', upper='2', enabled=True)

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'config.json'
        self.state = Path(self.tmp.name) / 'state.json'
        self.raw = dict(wallet=WALLET, monitors=[copy.deepcopy(ITEM)], poll_interval_seconds=60)
        self.path.write_text(json.dumps(self.raw))

    def run_sync(self, ids, liquidity, owner=WALLET, discovery_error=None, item_error=None):
        with ExitStack() as s:
            s.enter_context(patch.object(w.RPC, '__call__', return_value='0x100'))
            s.enter_context(patch.object(w, 'current_ids', return_value=ids, side_effect=discovery_error))
            s.enter_context(patch.object(w.chain, 'position_liquidity', side_effect=liquidity))
            s.enter_context(patch.object(w, 'owner_at', return_value=owner))
            s.enter_context(patch.object(w.chain, 'get_position_info', return_value={}))
            s.enter_context(patch.object(w, 'build_item', side_effect=item_error or (lambda tid, info, old: dict(ITEM, position_id=tid))))
            s.enter_context(patch.object(m, 'fetch_pair_price', return_value={'price': m.Decimal('1.5')}))
            return w.reconcile(self.path, self.state)

    def test_exit_all_allows_empty_and_no_price_requests(self):
        result = self.run_sync({1}, lambda *args: 0)
        self.assertEqual(result['removed_ids'], [1])
        config = m.normalize_config(json.loads(self.path.read_text()), self.path)
        self.assertEqual(config['monitors'], [])
        with patch.object(m, 'fetch_pair_price') as fetch, patch.object(m, 'send_wxpusher') as send:
            self.assertEqual(m.run_cycle(config, None, False, False, False), 0)
            fetch.assert_not_called(); send.assert_not_called()

    def test_add_new_remove_old(self):
        result = self.run_sync({1, 2}, lambda tid, block: 10 if tid == 2 else 0)
        self.assertEqual(result['active_ids'], [2])
        self.assertEqual(result['removed_ids'], [1])

    def test_rpc_unknown_preserves_old(self):
        def broken(*args): raise RuntimeError('timeout')
        result = self.run_sync({1}, broken)
        self.assertEqual(result['active_ids'], [1])
        self.assertTrue(result['errors'])

    def test_transferred_nft_removed(self):
        result = self.run_sync({1}, lambda *args: 9, owner='0x' + 'cd' * 20)
        self.assertEqual(result['active_ids'], [])

    def test_discovery_failure_still_checks_old_exit(self):
        result = self.run_sync(set(), lambda *args: 0, discovery_error=RuntimeError('index down'))
        self.assertEqual(result['active_ids'], [])
        self.assertFalse(result['discovery_ok'])
        self.assertNotIn('last_discovery_block', result)

    def test_unbuildable_new_item_remains_retryable(self):
        result = self.run_sync({1, 2}, lambda *args: 5, item_error=RuntimeError('no price'))
        self.assertEqual(result['active_ids'], [1])
        self.assertEqual(result['owned_ids'], [1, 2])
        self.assertEqual(len(result['errors']), 2)

    def test_same_pool_different_positions(self):
        self.raw['monitors'].append(dict(ITEM, position_id=2, lower='3', upper='4'))
        items = m.normalize_config(self.raw, self.path)['monitors']
        self.assertNotEqual(items[0]['id'], items[1]['id'])

    def test_notification_guard_closed_or_unknown_never_sends(self):
        cfg = m.normalize_config(self.raw, self.path)
        for effect in (False, RuntimeError('RPC timeout')):
            with patch.object(m, 'fetch_pair_price', return_value={'price': m.Decimal('3')}), \
                 patch.object(m, 'position_is_active', return_value=effect, side_effect=effect if isinstance(effect, Exception) else None), \
                 patch.object(m, 'send_wxpusher') as send:
                m.run_cycle(cfg, {}, False, False, False)
                send.assert_not_called()

    def test_guard_zero_liquidity(self):
        with patch.object(m, 'eth_call_rpc', return_value='0x0') as call:
            self.assertFalse(m.position_is_active(ITEM, WALLET))
            self.assertEqual(call.call_count, 1)

    def test_discovery_requires_balance_match(self):
        with patch.object(w, 'transfer_ids', return_value={1}), \
             patch.object(w, 'owner_at', return_value=WALLET), \
             patch.object(w.chain, 'balance_of', return_value=2), \
             patch.object(w, 'explorer_ids', return_value={1}):
            with self.assertRaises(w.sync.SyncError):
                w.current_ids(WALLET, self.raw, {}, lambda *a: None, 100)

    def test_explorer_pagination(self):
        def page(tid, next_page):
            return {'items': [{'id': str(tid), 'token': {'address_hash': w.chain.POSM}}], 'next_page_params': next_page}
        with patch.object(w.sync, 'request_json', side_effect=[page(1, {'id': 'next'}), page(2, None)]) as request:
            self.assertEqual(w.explorer_ids(WALLET), {1, 2})
            self.assertIn('&id=next', request.call_args_list[1].args[0])

    def test_lost_cursor_recovers_old_nfts_with_backfill(self):
        with patch.object(w, 'transfer_ids', side_effect=[{1}, {2}]), \
             patch.object(w, 'owner_at', return_value=WALLET), \
             patch.object(w.chain, 'balance_of', return_value=2), \
             patch.object(w, 'explorer_ids', side_effect=RuntimeError('index down')):
            self.assertEqual(w.current_ids(WALLET, self.raw, {}, lambda *a: None, 2000000), {1, 2})

if __name__ == '__main__':
    unittest.main(verbosity=2)
