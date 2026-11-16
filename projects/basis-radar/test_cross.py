"""Synthetic unit fixtures only; live acceptance uses real public APIs separately."""
import copy
import time
import unittest
from unittest.mock import patch
import server


def quote(venue, market, price=100.0, currency='USDT'):
    now=time.time()
    end=int(now)//60*60
    return {'venue':venue,'name':server.LABELS[venue],'market':market,'symbol':'BTCUSDT',
            'quote':currency,'bids':[[price,100]],'asks':[[price+0.01,100]],
            'candles':{end-i*60:price for i in range(1,121)},'received_at':now,
            'requested_at':now-0.1,'source_ts':now,'latency_ms':100,
            'history_error':None,'meta':{}}


class CrossTests(unittest.TestCase):
    def scan(self, quotes, **params):
        end=int(time.time())//60*60
        data={'quotes':quotes,'errors':[],'end':end,'fetched_at':time.time()}
        p={'symbol':['BTC'],'market':['spot_perp'],'venues':['binance,bybit'],
           'fee_binance_spot':['8'],'fee_binance_perp':['2'],
           'fee_bybit_spot':['9'],'fee_bybit_perp':['3']}
        p.update({k:[str(v)] for k,v in params.items()})
        with patch.object(server,'raw_data',return_value=data),patch.object(server,'live_history',return_value={}):
            return server.analyze(p)

    def test_cross_only_and_includes_same_exchange(self):
        data=self.scan([quote(v,m) for v in ['binance','bybit'] for m in ['spot','perp']])
        self.assertEqual(len(data['pairs']),4)
        for p in data['pairs']:
            self.assertEqual((p['market_a'],p['market_b']),('spot','perp'))
        same=[p for p in data['pairs'] if p['a']==p['b']]
        self.assertEqual(len(same),2)

    def test_market_specific_fee_and_direction_labels(self):
        data=self.scan([quote('binance','spot'),quote('bybit','perp',101)])
        p=data['pairs'][0]
        self.assertEqual(p['roundtrip_fee_bp'],22)
        self.assertIn('现货',p['name_a'])
        self.assertIn('永续',p['name_b'])
        directions=p['directions']
        self.assertEqual({r['buy_market'] for r in directions},{'spot','perp'})
        reverse=next(r for r in directions if r['sell_market']=='spot')
        self.assertIn('借币',reverse['risk_note'])
        forward=next(r for r in directions if r['sell_market']=='perp')
        self.assertIn('资金费',forward['risk_note'])

    def test_live_samples_use_actual_leg_markets(self):
        qs=[quote('binance','spot'),quote('binance','perp')]
        data={'quotes':qs,'errors':[],'end':int(time.time())//60*60,'fetched_at':time.time()}
        with patch.object(server,'raw_data',return_value=data),patch.object(server,'live_history',return_value={}) as hist:
            server.analyze({'symbol':['BTC'],'market':['spot_perp'],'venues':['binance']})
        self.assertEqual([c.args[1] for c in hist.call_args_list],['spot','perp'])

    def test_quote_currency_is_never_mixed(self):
        d=self.scan([quote('binance','spot'),quote('lighter','perp',currency='USD')])
        self.assertEqual(d['pairs'],[])
        self.assertEqual(len(d['skipped']),1)

    def test_same_market_modes_still_work(self):
        for market in ['spot','perp']:
            d=self.scan([quote('binance',market),quote('bybit',market)],market=market)
            self.assertEqual(len(d['pairs']),1)
            self.assertEqual(d['pairs'][0]['market_a'],market)

    def test_same_market_still_needs_two_venues(self):
        with self.assertRaises(ValueError):self.scan([],market='spot',venues='binance')
        d=self.scan([quote('binance','spot'),quote('binance','perp')],venues='binance')
        self.assertEqual(len(d['pairs']),1)

    def test_bad_market_rejected(self):
        with self.assertRaises(ValueError):self.scan([],market='futurez')


if __name__=='__main__':unittest.main()
