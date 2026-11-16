"""GET-only public market adapters. API keys/account endpoints deliberately absent."""
import concurrent.futures as cf
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

LABELS = {'binance': '币安', 'bybit': 'Bybit', 'bitget': 'Bitget', 'gate': 'Gate', 'lighter': 'Lighter', 'arcus': 'Arcus'}
_CACHE = {}
_LOCK = threading.Lock()

def get(url, params=None, ttl=0):
    if params:
        url += '?' + urllib.parse.urlencode(params)
    with _LOCK:
        old = _CACHE.get(url)
        if old and time.time() - old[0] < ttl:
            return old[1]
    req = urllib.request.Request(url, headers={'User-Agent': 'BasisRadar/1.0 (read-only)', 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            data = json.load(response)
    except urllib.error.HTTPError as e:
        raise ValueError('行情接口HTTP ' + str(e.code)) from None
    except Exception as e:
        raise ValueError('行情连接失败 ' + type(e).__name__) from None
    if ttl:
        with _LOCK:
            if len(_CACHE) > 500:
                _CACHE.clear()
            _CACHE[url] = (time.time(), data)
    return data


def _rows(data, t=0, c=4, scale=1000):
    return {int(float(x[t]) / scale): float(x[c]) for x in data}


def load(venue, symbol, market, end):
    start = time.time()
    quote = 'USDT'
    contract = symbol + 'USDT'
    mult = 1.0
    meta = {}
    if venue == 'binance':
        host = 'https://api.binance.com/api/v3' if market == 'spot' else 'https://fapi.binance.com/fapi/v1'
        info = get(host + '/exchangeInfo', ttl=3600)
        match = next((x for x in info['symbols'] if x['symbol'] == contract and x['baseAsset'] == symbol and x['quoteAsset'] == quote and x['status'] == 'TRADING' and (market == 'spot' or x.get('contractType') == 'PERPETUAL')), None)
        if not match:
            raise ValueError('没有完全匹配的交易对，不自动替换1000倍币或其他资产')
        def candles():
            return _rows(get(host + '/klines', {'symbol': contract, 'interval': '1m', 'startTime': (end-7200)*1000, 'endTime': end*1000-1, 'limit': 120}, ttl=40))
        def book():
            return get(host + '/depth', {'symbol': contract, 'limit': 100})
    elif venue == 'bybit':
        category = 'spot' if market == 'spot' else 'linear'
        info = get('https://api.bybit.com/v5/market/instruments-info', {'category': category, 'symbol': contract}, ttl=3600)
        match = next((x for x in info.get('result', {}).get('list', []) if x['baseCoin'] == symbol and x['quoteCoin'] == quote and x['status'] == 'Trading' and (market == 'spot' or x.get('contractType') == 'LinearPerpetual')), None)
        if not match:
            raise ValueError('没有完全匹配的交易对')
        def candles():
            data = get('https://api.bybit.com/v5/market/kline', {'category': category, 'symbol': contract, 'interval': '1', 'start': (end-7200)*1000, 'end': end*1000-1, 'limit': 120}, ttl=40)
            if data.get('retCode') != 0: raise ValueError('Bybit K线不可用')
            return _rows(data['result']['list'])
        def book():
            data = get('https://api.bybit.com/v5/market/orderbook', {'category': category, 'symbol': contract, 'limit': 50})
            if data.get('retCode') != 0: raise ValueError('Bybit盘口不可用')
            r = data['result']
            return {'bids': r['b'], 'asks': r['a'], 'ts': r.get('ts', data.get('time'))}
    elif venue == 'bitget':
        if market == 'spot':
            host = 'https://api.bitget.com/api/v2/spot/market'
            info = get('https://api.bitget.com/api/v2/spot/public/symbols', {'symbol': contract}, ttl=3600)
            match = next((x for x in info.get('data', []) if x['baseCoin'] == symbol and x['quoteCoin'] == quote and x['status'] == 'online'), None)
            if not match: raise ValueError('没有完全匹配的交易对')
            def candles():
                d = get(host + '/candles', {'symbol': contract, 'granularity': '1min', 'limit': 121}, ttl=40)
                if d.get('code') != '00000': raise ValueError('Bitget K线不可用')
                return _rows(d['data'])
            def book():
                d = get(host + '/orderbook', {'symbol': contract, 'type': 'step0', 'limit': 100})
                if d.get('code') != '00000': raise ValueError('Bitget盘口不可用')
                return d['data']
        else:
            host = 'https://api.bitget.com/api/v2/mix/market'
            info = get(host + '/contracts', {'symbol': contract, 'productType': 'USDT-FUTURES'}, ttl=3600)
            match = next((x for x in info.get('data', []) if x['baseCoin'] == symbol and x['quoteCoin'] == quote and x['symbolStatus'] == 'normal'), None)
            if not match: raise ValueError('没有完全匹配的交易对')
            def candles():
                d = get(host + '/candles', {'symbol': contract, 'productType': 'USDT-FUTURES', 'granularity': '1m', 'limit': 121}, ttl=40)
                if d.get('code') != '00000': raise ValueError('Bitget K线不可用')
                return _rows(d['data'])
            def book():
                d = get(host + '/merge-depth', {'symbol': contract, 'productType': 'USDT-FUTURES', 'precision': 'scale0', 'limit': 50})
                if d.get('code') != '00000': raise ValueError('Bitget盘口不可用')
                return d['data']
    elif venue == 'gate':
        contract = symbol + '_USDT'
        host = 'https://api.gateio.ws/api/v4'
        if market == 'spot':
            match = get(host + '/spot/currency_pairs/' + contract, ttl=3600)
            if match.get('base') != symbol or match.get('quote') != quote or match.get('trade_status') != 'tradable':
                raise ValueError('现货交易对不可用')
            def candles():
                d = get(host + '/spot/candlesticks', {'currency_pair': contract, 'interval': '1m', 'from': end-7200, 'to': end-1}, ttl=40)
                return _rows(d, c=2, scale=1)
            def book():
                return get(host + '/spot/order_book', {'currency_pair': contract, 'limit': 100, 'with_id': 'true'})
        else:
            match = get(host + '/futures/usdt/contracts/' + contract, ttl=3600)
            if match.get('in_delisting') or match.get('name') != contract:
                raise ValueError('合约不可用')
            mult = float(match['quanto_multiplier'])
            def candles():
                d = get(host + '/futures/usdt/candlesticks', {'contract': contract, 'interval': '1m', 'from': end-7200, 'to': end-1}, ttl=40)
                return {int(x['t']): float(x['c']) for x in d}
            def book():
                r = get(host + '/futures/usdt/order_book', {'contract': contract, 'limit': 100, 'with_id': 'true'})
                return {'bids': [[x['p'], float(x['s'])*mult] for x in r['bids']], 'asks': [[x['p'], float(x['s'])*mult] for x in r['asks']], 'current': r.get('current')}
    elif venue == 'lighter':
        if market != 'perp': raise ValueError('本版Lighter仅接永续，不与现货混比')
        host = 'https://mainnet.zklighter.elliot.ai/api/v1'
        info = get(host + '/orderBooks', ttl=600)
        match = next((x for x in info['order_books'] if x['symbol'] == symbol and x.get('market_type') == 'perp' and x.get('status') == 'active'), None)
        if not match: raise ValueError('没有完全匹配的永续市场')
        mid = match['market_id']; quote = 'USD'; contract = symbol + '-USD'
        def candles():
            d = get(host + '/candles', {'market_id': mid, 'resolution': '1m', 'start_timestamp': end-7200, 'end_timestamp': end-1, 'count_back': 120}, ttl=40)
            if d.get('code') != 200: raise ValueError('Lighter K线不可用')
            return {int(x['t'])//1000: float(x['c']) for x in d['c']}
        def book():
            r = get(host + '/orderBookOrders', {'market_id': mid, 'limit': 100})
            if r.get('code') != 200: raise ValueError('Lighter盘口不可用')
            return {'bids': [[x['price'], x['remaining_base_amount']] for x in r['bids']], 'asks': [[x['price'], x['remaining_base_amount']] for x in r['asks']]}
    elif venue == 'arcus':
        if market != 'perp': raise ValueError('本版Arcus仅接永续，不与现货混比')
        host = 'https://api.arcus.xyz/v1'
        info = get(host + '/markets', ttl=600)
        match = next((x for x in info['markets'] if x['baseAsset'] == symbol and x['quoteAsset'] == 'USD' and x['status'] == 'ONLINE' and x.get('type') == 'PERPETUAL'), None)
        if not match: raise ValueError('没有完全匹配的永续市场；不把ANTH、ANTHROPIC等名称自动视为同一资产')
        contract = match['marketDisplayName']; quote = 'USD'
        meta = {'outside_regular_hours': match.get('isOutsideRth'), 'funding_rate_raw': match.get('fundingRate'), 'next_funding_at': match.get('nextFundingAt')}
        def candles():
            r = get(host + '/candles', {'market': contract, 'timeframe': '1m', 'from': (end-7200)*1000000, 'to': end*1000000-1, 'count': 120}, ttl=40)
            return {int(x['openTime'])//1000000: float(x['close']) for x in r['candles'] if x.get('isFinal') is not False}
        def book():
            return get(host + '/l2OrderBook/' + urllib.parse.quote(contract), {'depth': 100})
    else:
        raise ValueError('不支持的平台')
    def timed_book():
        begun = time.time(); d = book(); received = time.time()
        return d, begun, received
    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        hist = ex.submit(candles)
        book_f = ex.submit(timed_book)
        history_error = None
        try: history = hist.result()
        except Exception as e: history, history_error = {}, str(e)[:130]
        data, begun, received = book_f.result()
    def levels(side):
        out = [[float(x[0]), float(x[1])] for x in data[side] if float(x[0]) > 0 and float(x[1]) > 0]
        if any(not math.isfinite(p) or not math.isfinite(q) for p, q in out): raise ValueError('非有限盘口数据')
        return sorted(out, key=lambda x: x[0], reverse=side == 'bids')
    bids, asks = levels('bids'), levels('asks')
    if not bids or not asks or bids[0][0] >= asks[0][0]:
        raise ValueError('盘口为空或交叉，不能用来判断')
    ts = data.get('ts') or data.get('timestamp') or data.get('current')
    if ts is not None:
        ts = float(ts)
        while ts > 1e11: ts /= 1000
        if abs(time.time() - ts) > 15:
            raise ValueError('交易所盘口时间戳过期或时钟异常')
    return {'venue': venue, 'name': LABELS[venue], 'symbol': contract, 'market': market,
            'quote': quote, 'bids': bids, 'asks': asks,
            'candles': {int(k):v for k,v in history.items() if end-7200 <= int(k) < end},
            'history_error': history_error, 'received_at': received, 'requested_at': begun,
            'source_ts': ts, 'latency_ms': round((received-begun)*1000),
            'meta': meta, 'elapsed_ms': round((time.time()-start)*1000)}
