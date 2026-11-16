import unittest
import time
from core import pair_stats, vwap, spread_bp, validate_symbol

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.end = 120 * 60
        self.a = {i * 60: 100.0 for i in range(120)}
        self.b = {i * 60: 101.0 for i in range(120)}

    def stats(self, **kw):
        args = dict(candles_a=self.a, candles_b=self.b, end=self.end, bid_a=100, ask_a=100.01, bid_b=101.99, ask_b=102, fee_a=2, fee_b=3, reserve=1, close_spread=2)
        args.update(kw)
        return pair_stats(**args)

    def test_aligned_spreads_not_difference_of_medians(self):
        d = self.stats()
        self.assertAlmostEqual(d['median_bp'], spread_bp(100, 101))
        self.assertEqual(d['samples'], 120)
        self.assertTrue(d['history_ready'])

    def test_median_of_each_timestamp_ratio(self):
        a, b = {0: 1, 60: 100, 120: 1000}, {0: 2, 60: 100, 120: 500}
        d = self.stats(candles_a=a, candles_b=b)
        self.assertEqual(d['median_bp'], 0)
        self.assertFalse(d['history_ready'])

    def test_current_candle_and_old_are_excluded(self):
        a = dict(self.a, **{})
        b = dict(self.b, **{})
        a[-60] = 1; b[-60] = 100
        a[self.end] = 1; b[self.end] = 100
        self.assertEqual(self.stats(candles_a=a, candles_b=b)['samples'], 120)

    def test_cost_is_four_trades_and_exit_spread(self):
        d = self.stats()
        self.assertAlmostEqual(d['roundtrip_fee_bp'], 10)
        for row in d['directions']:
            self.assertAlmostEqual(row['net_space_bp'], row['gross_space_bp'] - 10 - 1 - 2)

    def test_buy_low_sell_high_direction(self):
        d = self.stats()
        self.assertEqual(d['directions'][0]['buy'], 'a')
        self.assertGreater(d['directions'][0]['gross_space_bp'], 0)
        self.assertLess(d['directions'][1]['gross_space_bp'], 0)

    def test_missing_matching_samples(self):
        b = {t + 30: x for t, x in self.b.items()}
        d = self.stats(candles_b=b)
        self.assertIsNone(d['median_bp'])
        self.assertFalse(d['history_ready'])
        self.assertEqual(d['directions'], [])

    def test_stale_history_not_ready(self):
        a = {t: x for t, x in self.a.items() if t < 6600}
        b = {t: x for t, x in self.b.items() if t < 6600}
        self.assertFalse(self.stats(candles_a=a, candles_b=b)['history_ready'])

    def test_vwap_and_depth(self):
        self.assertAlmostEqual(vwap([[100, 1], [102, 1]], 1.5), (100 + 51) / 1.5)
        self.assertIsNone(vwap([[100, 1]], 2))
        self.assertIsNone(vwap([], 1))

    def test_invalid_prices(self):
        self.assertRaises(ValueError, spread_bp, 0, 100)
        self.assertRaises(ValueError, spread_bp, float('nan'), 100)

    def test_symbol_validation_no_guessing(self):
        self.assertEqual(validate_symbol(' btc '), 'BTC')
        for s in ['../../etc/passwd', 'BTC/USDT', 'BTC;rm -rf', '', 'A'*31]:
            with self.assertRaises(ValueError): validate_symbol(s)

if __name__ == '__main__':
    unittest.main()
