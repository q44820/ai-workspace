"""Artificial offline samples for order acknowledgement. Not market evidence."""
import unittest
from unittest.mock import AsyncMock, patch
import test_execution_safety as harness

bot = harness.bot


class OrderAckTests(harness.ExecutionTests):
    def test_strategy_defaults_unchanged(self):
        cfg = bot.DEF_CFG
        self.assertEqual(cfg["confirmSec"], 0)
        self.assertEqual(cfg["openBp"], 25)
        self.assertEqual(cfg["stopBp"], 100)
        self.assertEqual(cfg["maxHoldMin"], 240)
        self.assertEqual(cfg["minProfitBp"], 3)
        self.assertEqual(cfg["minHoldSec"], 30)
        self.assertEqual(cfg["feeIo"], 0.009)
        self.assertEqual(cfg["feeLt"], 0.028)
        self.assertEqual(cfg["slipCapBp"], 30)
        self.assertEqual(bot.DEF_CFG["margin"], bot.NOTIONAL)

    def test_lighter_acceptance_is_not_a_fill_or_rejection(self):
        accepted = bot.lighter_submission(object(), type("R", (), {"code": 200, "tx_hash": "h"})(), None)
        self.assertTrue(accepted["accepted"])
        self.assertFalse(accepted["rejected"])
        self.assertNotIn("filled", accepted)
        rejected = bot.lighter_submission(None, None, "invalid signature")
        self.assertTrue(rejected["rejected"])
        self.assertFalse(rejected["accepted"])
        api_reject = bot.lighter_submission(None, type("R", (), {"code": 400})(), None)
        self.assertTrue(api_reject["rejected"])
        unknown = bot.lighter_submission(object(), type("R", (), {})(), None)
        self.assertFalse(unknown["rejected"])
        self.assertFalse(unknown["accepted"])

    async def test_delayed_official_fill_keeps_hedge_without_rescue(self):
        bot.S.cfg["confirmSec"] = 0
        original = self.lookup
        reads = {}

        async def delayed(i):
            reads[i["id"]] = reads.get(i["id"], 0) + 1
            if i["venue"] == "lt" and reads[i["id"]] < 3:
                self.assertTrue(all(not row[3] for row in self.sent))
                return {}
            return await original(i)

        self.v.lookup_order = delayed
        self.assertTrue(await self.drive())
        self.assertEqual([row[3] for row in self.sent], [False, False])
        self.assertEqual(self.q, [-.174, .174])
        self.assertFalse(bot.S.pos.get("opening_failed"))
        self.assertEqual(bot.S.mode, "live")
        self.assertGreaterEqual(reads[bot.S.intents[1]["id"]], 3)
        self.assertTrue(all(i["terminal_evidence"]["source"] == "official-order" and i["terminal_evidence"]["state"] == "filled" for i in bot.S.intents))

    async def test_explicit_ioc_zero_skips_second_leg(self):
        self.prepare_entry()
        self.first = "ioc_miss"
        bot.S.cfg["confirmSec"] = 0
        await self.open_signal()
        self.assertEqual([row[0] for row in self.sent], ["io"])
        self.assertEqual(bot.S.mode, "live")
        self.assertIsNone(bot.S.pos)
        self.assertEqual(bot.S.trades, [])
        self.assertEqual(bot.S.intents[-1]["terminal_evidence"]["state"], "iocCancelRejected")
        self.assertEqual(bot.S.intents[-1]["terminal_evidence"]["filled"], 0)

    async def test_explicit_margin_reject_still_stops(self):
        self.prepare_entry()
        self.first = "margin_reject"
        await self.open_signal()
        self.assertEqual(bot.S.mode, "stopped")
        self.assertEqual(len(self.sent), 1)
        self.assertIsNone(bot.S.pos)
        self.assertFalse(bot.finish_ioc_miss({"opening_pending": True, "first_open_intent": bot.S.intents[-1]["id"]}, bot.now_ms(), 0, 0))

    async def test_permanent_unknown_does_not_reject_resend_or_flatten(self):
        self.first = "unknown"
        self.assertFalse(await self.drive())
        intent = bot.S.intents[0]
        self.assertEqual(intent["status"], "unknown")
        self.assertTrue(intent.get("ack_timed_out"))
        self.assertNotIn("terminal_evidence", intent)
        sent = len(self.sent)
        self.assertFalse(await self.v.drive_to(0, 0, True, "残腿全平"))
        self.assertEqual(len(self.sent), sent)
        self.assertNotEqual(intent.get("status"), "rejected")

    async def test_partial_fill_uses_official_quantity(self):
        self.first = "partial"
        self.assertFalse(await self.drive())
        self.assertEqual(len(self.sent), 1)
        evidence = bot.S.intents[0]["terminal_evidence"]
        self.assertAlmostEqual(evidence["filled"], .087)
        self.assertNotEqual(evidence["state"], "filled")
        self.assertAlmostEqual(self.q[0], -.087)
        self.assertEqual(self.q[1], 0)

    async def test_duplicate_official_reads_do_not_resend(self):
        self.assertTrue(await self.drive())
        for _ in range(3):
            await self.v.reconcile_orders(tuple(self.q))
            self.assertTrue(await self.drive())
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(bot.S.trades, [])

    async def test_restart_reads_pending_before_send(self):
        self.first = "unknown"
        await self.drive()
        bot.S.save()
        bot.S = bot.State()
        self.v._lookup_at.clear()
        events = []

        async def lookup(i):
            events.append("read")
            return {}

        self.v.lookup_order = lookup
        await self.drive()
        self.assertEqual(events, ["read"])
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(bot.unknown_orders())

    async def test_forged_fill_is_not_accepted(self):
        self.first = "unknown"
        await self.drive()
        intent = bot.S.intents[0]
        intent["status"] = "terminal"
        intent["terminal_evidence"] = dict(state="filled", filled=.174)
        self.v.lookup_order = AsyncMock(return_value={})
        self.v._lookup_at.clear()
        await self.v.reconcile_orders(tuple(self.q))
        self.assertNotIn("terminal_evidence", intent)
        self.assertEqual(intent["status"], "unknown")
        self.assertEqual(len(self.sent), 1)

    async def test_late_official_hedge_is_not_flattened(self):
        self.second = "unknown"
        self.assertFalse(await self.drive())
        self.assertEqual([row[:1] + row[3:] for row in self.sent], [("io", False), ("lt", False)])
        self.assertEqual(self.q, [-.174, .174])
        self.v.lookup_order = self.lookup
        self.v._lookup_at.clear()
        self.v._exposure_at = None
        bot.S.pos.update(t=bot.now_ms() - 1000, spread=0., opening_pending=True, opening_failed=True, live=True)
        bot.S.exit_pending = dict(submitted=True, why="开仓")
        await bot.reconcile_exit(bot.now_ms())
        self.assertEqual(len(self.sent), 2)
        self.assertFalse(bot.S.pos.get("rescue_pending"))
        self.assertFalse(bot.S.pos.get("opening_failed"))
        self.assertEqual(bot.S.mode, "live")
        self.assertFalse(bot.S.pos.get("entryAccountingUnknown"))
        self.assertFalse(bot.S.pos.get("entryFeesKnown"))

    async def test_emergency_close_late_fill_is_unreconciled(self):
        self.q[:] = [-.174, .174]
        original = self.lookup
        count = {"n": 0}

        async def delayed(i):
            count["n"] += 1
            if count["n"] < 3:
                return {}
            return await original(i)

        self.v.lookup_order = delayed
        self.v.fills = AsyncMock(side_effect=TimeoutError("fills delayed"))
        bot.S.pos.update(t=bot.now_ms() - 1000, spread=0., N=290., pxIo=1700., pxLt=1660., live=True)
        with patch.object(bot.asyncio, "sleep", new=AsyncMock()):
            await bot.close_round(bot.now_ms(), 0., "回归中位数")
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(all(row[3] for row in self.sent))
        self.assertEqual(self.q, [0., 0.])
        self.assertIsNone(bot.S.pos)
        self.assertEqual(bot.S.mode, "live")
        trade = bot.S.trades[-1]
        self.assertEqual(trade["status"], "unreconciled")
        self.assertIsNone(trade["u"])
        self.assertFalse(trade["real"])
        bot.S.trades.append(dict(real=False, status="unreconciled", u=12.5))
        bot.S.trades.append(dict(real=True, status="reconciled", u=1.0))
        self.assertEqual(bot.public_snapshot()["totals"]["net"], 1.0)

    async def test_risk_close_unknown_after_wait_stays_stopped(self):
        self.q[:] = [-.174, .174]
        self.v.lookup_order = AsyncMock(return_value={})
        bot.S.pos.update(t=bot.now_ms() - 1000, spread=0., N=290., live=True)
        await bot.close_round(bot.now_ms(), 0., "止损")
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(bot.unknown_orders())
        self.assertTrue(all(i.get("ack_timed_out") for i in bot.S.intents))
        self.assertTrue(all("terminal_evidence" not in i for i in bot.S.intents))
        self.assertEqual(bot.S.mode, "stopped")
        self.assertIsNotNone(bot.S.pos)
        sent = len(self.sent)
        await bot.close_round(bot.now_ms(), 0., "止损")
        self.assertEqual(len(self.sent), sent)


if __name__ == "__main__":
    unittest.main(verbosity=2)
