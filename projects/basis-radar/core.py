"""Pure basis math. No order/account API exists in this project."""
import math
import re
import statistics


def validate_symbol(value):
    value = value.strip().upper()
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9._-]{0,29}', value):
        raise ValueError('请输入明确的币种代码，例如 BTC；不要填合约地址或交易对')
    return value


def spread_bp(a, b):
    if not all(math.isfinite(x) and x > 0 for x in (a, b)):
        raise ValueError('invalid price')
    return 20000 * (b - a) / (a + b)


def vwap(levels, qty):
    if not math.isfinite(qty) or qty <= 0:
        return None
    left, total = qty, 0.0
    for price, size in levels:
        price, size = float(price), float(size)
        if not math.isfinite(price) or not math.isfinite(size) or price <= 0 or size <= 0:
            continue
        take = min(left, size)
        total += take * price
        left -= take
        if left <= max(1e-10 * qty, 1e-14):
            return total / qty
    return None


def pair_stats(candles_a, candles_b, end, bid_a, ask_a, bid_b, ask_b,
               fee_a, fee_b, reserve, close_spread):
    times = sorted(t for t in candles_a.keys() & candles_b.keys()
                   if end - 7200 <= t < end and
                   all(math.isfinite(float(v)) and float(v) > 0
                       for v in (candles_a[t], candles_b[t])))
    values = [spread_bp(float(candles_a[t]), float(candles_b[t])) for t in times]
    med = statistics.median(values) if values else None
    ready = bool(len(times) >= 108 and times[-1] >= end - 120
                 and times[0] <= end - 6600)
    result = {'median_bp': med, 'samples': len(times), 'coverage_pct': len(times) / 120 * 100,
              'history_ready': ready, 'first_ts': times[0] if times else None,
              'last_ts': times[-1] if times else None,
              'mad_bp': statistics.median(abs(v - med) for v in values) if values else None,
              'roundtrip_fee_bp': 2 * (fee_a + fee_b), 'directions': [],
              'series': [[t, v] for t, v in zip(times, values)]}
    if med is None:
        return result
    for buy, sell, entry in [('a', 'b', spread_bp(ask_a, bid_b)),
                             ('b', 'a', spread_bp(ask_b, bid_a))]:
        target = med if buy == 'a' else -med
        gross = entry - target
        result['directions'].append({'buy': buy, 'sell': sell, 'entry_bp': entry,
                                     'target_bp': target, 'gross_space_bp': gross,
                                     'net_space_bp': gross - 2 * (fee_a + fee_b) - reserve - close_spread})
    return result
