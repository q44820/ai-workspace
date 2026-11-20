"""Offline regressions for historical order reconciliation and the combined loss limit."""
import json, sys, unittest
from pathlib import Path
from unittest.mock import AsyncMock
import test_execution_safety as harness

bot = harness.bot


def intent(venue, purpose, t, official_id, filled, source=None, status="terminal", direction=None):
    evidence = dict(state="filled", filled=filled)
    if source:
        evidence["source"] = source
        evidence["oid"] = 100
    return dict(id=str(official_id), venue=venue, direction=direction or ("sell" if venue == "io" else "buy"),
                qty=filled, purpose=purpose, status=status, t=t, official_id=official_id,
                account="test-account" if venue == "io" else 123, market=bot.COIN if venue == "io" else bot.MID,
                reduce=purpose == "平仓", pre=dict(io=0.0, lt=0.0), submitted=True, terminal_evidence=evidence)


COLLECTION = dict(chain="ops-readonly-authorized", reader="t217-official-history-audit",
                  queries=["account_inactive_orders", "trades"])


def official_record(official_id, sizes):
    return dict(venue="lt", official_id=official_id, collection=COLLECTION,
                order=dict(status="filled", client_order_index=official_id, filled_base_amount=sum(sizes), order_index="oid"),
                trades=[dict(size=size) for size in sizes])


def backup_round(base):
    """The 2026-11-21 03:34 backup shape: a completed open and close, all terminal, flat now."""
    return [
        intent("lt", "开仓", base + 1000, 113917744074459, 0.174),
        intent("lt", "平仓", base + 2000, 204835031781534, 0.174, direction="sell"),
        intent("io", "开仓", base + 3000, "0xd3f20e389bb4ce210e72c85907601ece", 0.126, direction="sell"),
        intent("lt", "开仓", base + 4000, 13096582632427, 0.126),
        intent("io", "平仓", base + 5000, "0x1399a7be11ef231276f8de0521c0bbbc", 0.126, direction="buy"),
        intent("lt", "平仓", base + 6000, 98398539876166, 0.126, direction="sell"),
    ]


class HistoryReconcileTests(harness.ExecutionTests):
    def snapshot(self):
        return [(i["official_id"], i["status"], None if not i.get("terminal_evidence") else i["terminal_evidence"].get("filled")) for i in bot.S.intents]

    async def test_flat_history_survives_restart_and_full_reconcile(self):
        base = bot.now_ms() - 86400000
        bot.S.intents = backup_round(base)
        before = self.snapshot()
        self.q[:] = [0.0, 0.0]
        self.v.lookup_order = AsyncMock(return_value={})
        await self.v.reconcile_orders(tuple(self.q))
        bot.S.save()
        bot.S = bot.State()
        self.v._lookup_at.clear()
        await self.v.reconcile_orders((0.0, 0.0))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(bot.unknown_orders())
        self.assertTrue(all(i.get("terminal_evidence", {}).get("filled") for i in bot.S.intents))

    async def test_old_gate_does_not_fabricate_submitted_exit(self):
        base = bot.now_ms() - 86400000
        bot.S.intents = backup_round(base)
        latest = intent("io", "开仓", bot.now_ms() - 1000, "0xunconfirmed", 0.188, direction="sell")
        latest["terminal_evidence"] = dict(state="filled", filled=0.188)
        bot.S.intents.append(latest)
        self.v.lookup_order = AsyncMock(return_value={})
        self.prepare_entry()
        await self.open_signal()
        self.assertIsNone(bot.S.pos)
        self.assertFalse((bot.S.exit_pending or {}).get("submitted"))
        self.assertEqual(self.sent, [])
        self.assertEqual(bot.S.mode, "stopped")
        self.assertEqual(bot.S.intents[-1]["status"], "unknown")
        self.assertNotIn("terminal_evidence", bot.S.intents[-1])
        self.assertTrue(all(i["status"] == "terminal" and i.get("terminal_evidence") for i in bot.S.intents[:-1]))

    async def test_historical_flat_round_can_open_without_reviving_old_orders(self):
        base = bot.now_ms() - 86400000
        bot.S.intents = backup_round(base)
        ids = {i["official_id"] for i in bot.S.intents}
        original = self.lookup
        async def lookup(i):
            if i["official_id"] in ids:
                return {}
            return await original(i)
        self.v.lookup_order = lookup
        self.prepare_entry()
        await self.open_signal()
        self.assertGreater(len(self.sent), 0)
        self.assertTrue(ids < {i["official_id"] for i in bot.S.intents})
        old = [i for i in bot.S.intents if i["official_id"] in ids]
        self.assertTrue(all(i["status"] == "terminal" and i.get("terminal_evidence", {}).get("filled") for i in old))
        self.assertFalse(any(i["status"] in ("unknown", "terminal_wait") for i in old))

    async def test_polluted_terminal_wait_is_not_forged_and_unknown_stays(self):
        base = bot.now_ms() - 86400000
        rows = backup_round(base)
        rows[0]["status"] = "unknown"
        rows[0].pop("terminal_evidence")
        rows[2]["status"] = "terminal_wait"
        rows[2]["terminal_evidence"] = dict(state="filled", filled=0.126, source="official-order", oid=611415071765)
        bot.S.intents = rows
        bot.S.pos = dict(dir="S", t=bot.now_ms(), opening_pending=True, entryAccountingUnknown=True, live=True, qIo=0.188, qLt=0.188)
        bot.S.exit_pending = dict(submitted=True, why="开仓", t=bot.now_ms())
        self.v.lookup_order = AsyncMock(return_value={})
        await self.v.reconcile_orders((0.0, 0.0))
        self.assertEqual(bot.S.intents[0]["status"], "unknown")
        self.assertNotIn("terminal_evidence", bot.S.intents[0])
        self.assertEqual(bot.S.intents[2]["status"], "terminal")
        self.assertEqual(bot.S.intents[2]["terminal_evidence"]["filled"], 0.126)
        self.assertEqual(len(bot.S.intents), 6)
        phantom = bot.S.pos
        await bot.open_round("S", bot.now_ms(), 250.0, -50.0)
        self.assertEqual(self.sent, [])
        self.assertIs(bot.S.pos, phantom)
        self.assertEqual(bot.S.intents[0]["status"], "unknown")
        self.assertEqual(bot.S.mode, "live")
        self.assertIn("Pending order reconciliation", bot.S.alert)

    def test_audit_restore_requires_ops_query_not_a_typed_number(self):
        current = dict(official_id=113917744074459, venue="lt", status="unknown")
        backup = intent("lt", "开仓", 1, 113917744074459, 0.174)
        official = official_record(113917744074459, (0.11397, 0.06003))
        self.assertIsNone(bot.audit_restore_intent(current, backup, None))
        self.assertIsNone(bot.audit_restore_intent(current, backup, 0.174))
        self.assertIsNone(bot.audit_restore_intent(current, backup, dict(source="official-order", official_id=113917744074459, venue="lt", filled=0.174)))
        self.assertIsNone(bot.audit_restore_intent(current, backup, dict(official, source="manual")))
        self.assertIsNone(bot.audit_restore_intent(current, backup, official_record(113917744074459, (0.1, 0.07))))
        self.assertIsNone(bot.audit_restore_intent(current, backup, official_record(1, (0.11397, 0.06003))))
        restored = bot.audit_restore_intent(current, backup, official)
        self.assertEqual(restored["status"], "terminal")
        self.assertEqual(restored["terminal_evidence"]["filled"], 0.174)
        self.assertEqual(restored["terminal_evidence"]["source"], "official-order")
        self.assertEqual(restored["audit_note"], "proposal-only-from-authorized-readonly-fixture")
        self.assertEqual(current["status"], "unknown")
        self.assertNotIn("terminal_evidence", current)
        held = dict(current, terminal_evidence=dict(source="official-order", filled=0.174))
        self.assertIsNone(bot.audit_restore_intent(held, backup, official))
        import subprocess, tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, payload in (("current.json", current), ("backup.json", backup)):
                (root / name).write_text(json.dumps(payload))
            (root / "typed.json").write_text("0.174")
            (root / "official.json").write_text(json.dumps(official))
            script = bot.HERE / "audit_restore_offline.py"
            refused = subprocess.run([sys.executable, str(script), str(root / "current.json"), str(root / "backup.json"), str(root / "typed.json")], capture_output=True, text=True)
            self.assertEqual(refused.returncode, 2)
            self.assertIn("refuse", refused.stderr + refused.stdout)
            proposed = subprocess.run([sys.executable, str(script), str(root / "current.json"), str(root / "backup.json"), str(root / "official.json")], capture_output=True, text=True)
            self.assertEqual(proposed.returncode, 0, proposed.stderr)
            body = json.loads(proposed.stdout)
            self.assertTrue(body["ok"])
            self.assertFalse(body["wrote"])
            self.assertEqual(body["terminal_evidence"]["filled"], 0.174)
            self.assertFalse((root / "state.json").exists())

    def test_real_withdraw_cursor_cycle_is_not_a_verified_zero(self):
        cursor = "c" * 32
        pages = [dict(code=200, cursor=cursor, withdraws=[]) for _ in range(8)]
        walked = bot.assemble_cursor_pages(pages, "withdraws")
        self.assertFalse(walked["complete"])
        self.assertEqual(walked["reason"], "cursor-cycle")
        self.assertEqual(walked["pages_used"], 2)
        self.assertTrue(walked["empty_pages"])
        self.assertIsNone(bot.verified_lighter_withdrawals(walked, None))
        ended = bot.assemble_cursor_pages([
            dict(code=200, cursor="next", deposits=[dict(id="d1", asset_id=3, timestamp=1, status="completed", amount="1")]),
            dict(code=200, cursor="", deposits=[dict(id="d1", asset_id=3, timestamp=1, status="completed", amount="1")]),
        ], "deposits")
        self.assertTrue(ended["complete"])
        self.assertEqual(len(ended["rows"]), 1)
        transfers = bot.assemble_cursor_pages([
            dict(code=200, cursor="t", transfers=[]),
            dict(code=200, cursor="", transfers=[]),
        ], "transfers")
        self.assertTrue(transfers["complete"])
        self.assertEqual(transfers["rows"], [])
        conflict = bot.assemble_cursor_pages([
            dict(code=200, cursor="a", deposits=[dict(id="d1", amount="1")]),
            dict(code=200, cursor="", deposits=[dict(id="d1", amount="2")]),
        ], "deposits")
        self.assertFalse(conflict["complete"])
        self.assertEqual(conflict["reason"], "id-conflict")

    def test_explorer_short_page_can_cover_only_the_empty_cursor_cycle(self):
        cursor = "c" * 32
        primary = bot.assemble_cursor_pages([dict(code=200, cursor=cursor, withdraws=[]), dict(code=200, cursor=cursor, withdraws=[])], "withdraws")
        since = 1_700_000_000_000
        empty_logs = bot.assemble_explorer_withdrawals([[]], 7, 3, since)
        self.assertTrue(empty_logs["complete"])
        self.assertEqual(bot.verified_lighter_withdrawals(primary, empty_logs), [])
        filled = bot.assemble_explorer_withdrawals([[{
            "hash": "h1", "pubdata_type": "WithdrawV2", "status": "executed", "time": since + 1000,
            "pubdata": {"withdraw_pubdata_v2": {"amount": "1.5", "asset_index": "3", "from_account_index": "7", "route_type": "fast"}},
        }]], 7, 3, since)
        rows = bot.verified_lighter_withdrawals(primary, filled)
        self.assertEqual(rows[0]["status"], "completed")
        self.assertEqual(bot.classify_lt_cashflow([], rows, [], 7, 3, since), -1.5)
        full = [dict(hash=str(i), pubdata_type="Withdraw", status="executed", time=since - 1000,
                     pubdata={"withdraw_pubdata": {"usdc_amount": "1", "from_account_index": "7"}}) for i in range(bot.EXPLORER_LOG_LIMIT)]
        truncated = bot.assemble_explorer_withdrawals([full], 7, 3, since)
        self.assertFalse(truncated["complete"])
        self.assertIsNone(bot.verified_lighter_withdrawals(primary, truncated))
        partial = bot.assemble_cursor_pages([
            dict(code=200, cursor="a", withdraws=[dict(id="w", asset_id=3, timestamp=2000, status="completed", amount="1")]),
            dict(code=200, cursor="a", withdraws=[]),
        ], "withdraws")
        self.assertIsNone(bot.verified_lighter_withdrawals(partial, empty_logs))

    def test_hl_send_before_baseline_is_ignored_and_inside_window_fails_closed(self):
        since = 1_700_000_000_000
        send = dict(time=since - 1, hash="0x" + "ab" * 32, delta={
            "type": "send", "user": "0xabc", "destination": "0xdef", "sourceDex": "", "destinationDex": "spot",
            "token": "USDC", "amount": "12.5", "usdcValue": "12.5", "fee": "0", "feeToken": "", "nonce": 1,
        })
        deposit = dict(time=since, delta={"type": "deposit", "usdc": "4"})
        self.assertEqual(bot.classify_hl_cashflow([send, deposit], "0xabc", since), 4)
        self.assertIsNone(bot.classify_hl_cashflow([dict(send, time=since)], "0xabc", since))
        both = [dict(time=since, delta={"type": "internalTransfer", "usdc": "2", "user": "0xabc", "destination": "0xdef"}), dict(send, time=since)]
        self.assertIsNone(bot.classify_hl_cashflow(both, "0xabc", since))
        now = bot.now_ms()
        bot.S.loss = bot.default_loss_state()
        bot.observe_loss_sample(100, 100, now, now, 0.0, now)
        baseline = dict(bot.S.loss["baseline"])
        bot.observe_loss_sample(100, 100, now, now, None, now)
        self.assertEqual(bot.S.loss["baseline"], baseline)
        self.assertFalse(bot.S.loss["ready"])
        self.assertIsNone(bot.S.loss.get("resetAt"))

    def test_restore_proposal_keeps_every_intent_and_clears_marker_only_with_window(self):
        base = 1_794_902_400_000
        rows = backup_round(base)
        current_rows = [dict(row) for row in rows]
        for row in current_rows[:2]:
            row["status"] = "unknown"
            row.pop("terminal_evidence")
        for row in current_rows[2:4]:
            row["status"] = "terminal_wait"
        marker_t = 1_795_203_429_611
        current = dict(mode="live", intents=current_rows, exit_pending=dict(submitted=True, why="开仓", t=marker_t),
                       pos=dict(dir="S", opening_pending=True, entryAccountingUnknown=True, first_open_intent=None))
        backup = dict(intents=rows)
        official = dict(collection=COLLECTION, records=[
            official_record(113917744074459, (0.11397, 0.06003)),
            official_record(204835031781534, (0.12441, 0.04959)),
        ])
        window = dict(collection=COLLECTION, start_ms=1_795_203_000_000, end_ms=1_795_205_107_000,
                      positions=dict(io=0, lt=0),
                      venues=dict(io=dict(code=200, orders=0, fills=0), lt=dict(code=200, orders=0, fills=0, active_orders=0)))
        missing = bot.propose_polluted_state(current, backup, official, {})
        self.assertTrue(missing["ok"])
        self.assertEqual(missing["restored_ids"], ["113917744074459", "204835031781534"])
        self.assertFalse(missing["marker"]["cleared"])
        self.assertEqual(missing["proposed_intents"][2]["status"], "terminal_wait")
        self.assertEqual(missing["proposed_exit_pending"]["submitted"], True)
        self.assertEqual(missing["intent_count"], 6)
        self.assertFalse(missing["loadable_state"])
        opened = bot.propose_polluted_state(current, backup, official, window)
        self.assertTrue(opened["marker"]["cleared"])
        self.assertIsNone(opened["proposed_pos"])
        self.assertIsNone(opened["proposed_exit_pending"])
        self.assertEqual(opened["mode"], "live")
        self.assertIn("current", opened["diff"])
        self.assertFalse(bot.propose_polluted_state(current, backup, dict(official, records=[dict(official_id=113917744074459, venue="lt", filled=0.174)]), window)["ok"])
        import subprocess, tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {}
            for name, payload in (("current.json", current), ("backup.json", backup), ("official.json", official), ("window.json", window)):
                paths[name] = root / name
                paths[name].write_text(json.dumps(payload))
            script = bot.HERE / "restore_state_offline.py"
            dry = subprocess.run([sys.executable, str(script), *[str(paths[name]) for name in ("current.json", "backup.json", "official.json", "window.json")]], capture_output=True, text=True)
            self.assertEqual(dry.returncode, 0, dry.stderr)
            body = json.loads(dry.stdout)
            self.assertTrue(body["dry_run"])
            self.assertFalse(body["proposal_file_written"])
            self.assertEqual(set(root.iterdir()), set(paths.values()))
            target = root / "proposal.json"
            written = subprocess.run([sys.executable, str(script), *[str(paths[name]) for name in paths], "--output", str(target)], capture_output=True, text=True)
            self.assertEqual(written.returncode, 0, written.stderr)
            self.assertTrue(target.exists())
            self.assertTrue(target.read_text().startswith("ANTH_RESTORE_PROPOSAL"))
            self.assertEqual(json.loads((root / "current.json").read_text())["intents"][0]["status"], "unknown")
            blocked = subprocess.run([sys.executable, str(script), *[str(paths[name]) for name in paths], "--output", str(root / "current.json")], capture_output=True, text=True)
            self.assertEqual(blocked.returncode, 2)
            self.assertEqual(json.loads((root / "current.json").read_text())["intents"][0]["status"], "unknown")

    async def test_loss_default_blocks_until_fresh_bilateral_equity(self):
        bot.S.loss = bot.default_loss_state()
        self.prepare_entry()
        await self.open_signal()
        self.assertEqual(self.sent, [])
        self.assertIsNone(bot.S.pos)
        self.assertIn("亏损基线未初始化", bot.S.alert)
        now = bot.now_ms()
        bot.observe_loss_sample(100, 100, now, now, 0.0, now)
        self.assertEqual(bot.S.loss["limitUsd"], 5.0)
        self.assertEqual(bot.S.loss["netLoss"], 0.0)
        self.assertFalse(bot.S.loss["triggered"])

    async def test_loss_includes_fees_unrealized_and_opposing_venues(self):
        now = bot.now_ms()
        bot.S.loss = bot.default_loss_state()
        bot.observe_loss_sample(100, 100, now, now, 0.0, now)
        bot.observe_loss_sample(97, 102, now, now, 0.0, now)
        self.assertAlmostEqual(bot.S.loss["netLoss"], 1.0)
        bot.observe_loss_sample(90, 104, now, now, 0.0, now)
        self.assertTrue(bot.S.loss["triggered"])
        self.assertAlmostEqual(bot.S.loss["triggeredLoss"], 6.0)
        mode = bot.S.mode
        bot.observe_loss_sample(100, 100, now, now, 0.0, now)
        self.assertTrue(bot.S.loss["triggered"])
        self.assertEqual(bot.S.mode, mode)
        self.prepare_entry()
        await self.open_signal()
        self.assertEqual(self.sent, [])
        self.assertIsNone(bot.S.pos)

    def test_cashflow_adjustment_and_fail_closed(self):
        now = bot.now_ms()
        bot.S.loss = bot.default_loss_state()
        bot.observe_loss_sample(100, 100, now, now, 0.0, now)
        bot.observe_loss_sample(110, 100, now, now, 10.0, now)
        self.assertAlmostEqual(bot.S.loss["netLoss"], 0.0)
        self.assertFalse(bot.S.loss["triggered"])
        bot.observe_loss_sample(90, 100, now, now, None, now)
        self.assertFalse(bot.S.loss["ready"])
        self.assertIn("异常", bot.S.loss["anomaly"])
        self.assertTrue(bot.S.loss["baseline"])
        skewed = bot.fresh_equity_pair(100, 100, now, now - 5000, now)
        self.assertIsNone(skewed)
        self.assertIsNone(bot.hl_equity_value("dexAbstraction", 1, 1, 1))
        self.assertEqual(bot.hl_equity_value("unifiedAccount", 0, 0, 50), 50)
        self.assertEqual(bot.hl_equity_value("disabled", 40, 10, 999), 50)
        self.assertIsNone(bot.classify_hl_cashflow([{"delta": {"type": "spotTransfer", "usdc": "1"}}], "0xabc"))
        self.assertEqual(bot.classify_hl_cashflow([
            {"delta": {"type": "deposit", "usdc": "5"}},
            {"delta": {"type": "internalTransfer", "usdc": "2", "user": "0xabc", "destination": "0xdef"}},
        ], "0xabc"), 3)
        since = now - 1000
        self.assertEqual(bot.classify_lt_cashflow(
            [dict(asset_id=3, timestamp=now, status="completed", amount="4")],
            [dict(asset_id=3, timestamp=now, status="completed", amount="1")],
            [dict(asset_id=3, timestamp=now, type="L2TransferInflow", amount="2", fee="0", to_account_index=7, from_account_index=8)],
            7, 3, since), 5)
        self.assertIsNone(bot.classify_lt_cashflow(
            [dict(asset_id=3, timestamp=now, status="pending", amount="4")], [], [], 7, 3, since))

    async def test_limit_edit_persists_and_reset_does_not_resume(self):
        now = bot.now_ms()
        bot.S.loss = bot.default_loss_state()
        bot.observe_loss_sample(100, 100, now, now, 0.0, now)
        bot.observe_loss_sample(96, 100, now, now, 0.0, now)
        bot.S.mode = "live"
        bot.set_loss_limit(3, now)
        self.assertTrue(bot.S.loss["triggered"])
        self.assertEqual(bot.S.mode, "live")
        bot.set_loss_limit(50, now)
        self.assertTrue(bot.S.loss["triggered"])
        self.assertEqual(bot.S.mode, "live")
        bot.S.save()
        bot.S = bot.State()
        self.assertTrue(bot.S.loss["triggered"])
        self.assertEqual(bot.S.loss["limitUsd"], 50)
        self.assertEqual(bot.S.loss["baseline"]["total"], 200)
        self.assertFalse(bot.S.loss["ready"])
        self.assertEqual(bot.S.mode, "paper")
        stale = bot.now_ms() - EQUITY_STALE
        bot.observe_loss_sample(96, 100, stale, stale, 0.0, stale)
        self.assertFalse(bot.reset_loss_baseline())
        fresh = bot.now_ms()
        bot.observe_loss_sample(96, 100, fresh, fresh, 0.0, fresh)
        self.assertTrue(bot.reset_loss_baseline(fresh))
        self.assertFalse(bot.S.loss["triggered"])
        self.assertEqual(bot.S.mode, "paper")
        self.assertEqual(bot.S.loss["netLoss"], 0.0)

    async def test_triggered_loss_still_reduces_and_both_gates_block_opens(self):
        now = bot.now_ms()
        bot.observe_loss_sample(90, 100, now, now, 0.0, now)
        self.assertTrue(json.loads(bot.STATE_F.read_text())["loss"]["triggered"])
        self.q[:] = [-0.174, 0.174]
        self.assertTrue(await self.v.drive_to(0, 0, True, "止损"))
        self.assertTrue(self.sent and all(row[3] for row in self.sent))
        sent = len(self.sent)
        self.assertFalse(await self.v.drive_to(-0.348, 0.348, False, "开仓"))
        self.assertEqual(len(self.sent), sent)
        self.prepare_entry()
        await self.open_signal()
        self.assertEqual(len(self.sent), sent)
        self.assertTrue(bot.S.loss["triggered"])

    async def test_loss_api_rejects_bad_amounts_and_unconfirmed_reset(self):
        class Req:
            headers = {}
            def __init__(self, body): self.body = body
            async def json(self): return self.body
        now = bot.now_ms()
        bot.S.loss = bot.default_loss_state()
        bot.observe_loss_sample(100, 100, now, now, 0.0, now)
        for bad in (0, -1, "nope", None, 1e9, True):
            response = await bot.h_loss_limit(Req(dict(limitUsd=bad)))
            payload = json.loads(response.body.decode())
            self.assertIn("金额", payload["err"])
            self.assertEqual(bot.S.loss["limitUsd"], 5.0)
        ok = json.loads((await bot.h_loss_limit(Req(dict(limitUsd="4.5")))).body.decode())
        self.assertEqual(ok["loss"]["limitUsd"], 4.5)
        self.assertEqual(bot.S.loss["limitUsd"], 4.5)
        denied = json.loads((await bot.h_loss_reset(Req(dict(confirm=False)))).body.decode())
        self.assertIn("确认", denied["err"])
        self.assertIsNotNone(bot.S.loss["baseline"])
        page = (bot.HERE / "control.html").read_text()
        self.assertIn("保存金额", page)
        self.assertIn("/api/loss-limit", page)
        self.assertIn("重置亏损基线", page)
        self.assertIn("不是亏损硬封顶", page)


EQUITY_STALE = bot.EQUITY_MAX_AGE_MS + 1


if __name__ == "__main__":
    unittest.main(verbosity=2)
