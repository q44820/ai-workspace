"""Offline fixtures and loopback HTTP only; no production data."""
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch

TMP = tempfile.TemporaryDirectory(prefix='arb-hub-test-')
os.environ['ARB_HUB_RUNTIME'] = TMP.name
import app
import official
import runtime
from http.server import ThreadingHTTPServer

def fixture():
    return dict(coin='TEST', strategy='spread', long_platform='Binance', short_platform='OKX', long_kind='现货', short_kind='永续', source_url='https://example.com/public', source_time='2026-01-01T00:00:00+00:00')

class HubTests(unittest.TestCase):
    def test_01_fresh_checkout_no_snapshot(self):
        self.assertEqual(app.load_rows(), [])
    def test_02_filter_fixture(self):
        rows = [app.normalize(fixture())]
        self.assertEqual(app.filter_rows(rows, strategy='spread', search='TEST'), rows)
        self.assertEqual(app.filter_rows(rows, strategy='spread', search='OTHER'), [])
    def test_03_aliases(self):
        self.assertEqual(app.canonical('HL'), 'Hyperliquid')
        self.assertEqual(app.canonical('OKX'), 'OKX')
    def test_04_cache_is_stale_until_fresh(self):
        cache=app.Cache(Path(TMP.name)/'fixture-cache.json')
        self.assertTrue(cache.view()['stale'])
        self.assertIsNotNone(runtime.zoned('2026-01-01T00:00:00+00:00'))
    def test_05_venue_parser_fixture(self):
        meta={'contractType':'PERPETUAL','status':'TRADING','quoteAsset':'USDT','marginAsset':'USDT','symbol':'TESTUSDT','baseAsset':'TEST','underlyingType':'COIN'}
        raw=[[{'symbol':'TESTUSDT','lastFundingRate':'0.0001','markPrice':'1'}],[{'symbol':'TESTUSDT','fundingIntervalHours':8}],{'symbols':[meta]},[{'symbol':'TESTUSDT','price':'1'}]]
        rows=official.parse_binance(raw)
        self.assertEqual(len(rows),2)
        self.assertTrue(all(r['base']=='TEST' for r in rows))
    def test_06_http_local_only(self):
        server=ThreadingHTTPServer(('127.0.0.1',0), app.Handler)
        server.import_lock=threading.Lock()
        server.cache=app.Cache(Path(TMP.name)/'http-pulse.json')
        server.hub=official.Hub(Path(TMP.name)/'http-official')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base='http://127.0.0.1:'+str(server.server_port)
        original_connect=socket.socket.connect
        def local_only(sock,address):
            if isinstance(address,tuple) and address[0] not in ('127.0.0.1','::1'):
                raise AssertionError('EXTERNAL_NETWORK_FORBIDDEN')
            return original_connect(sock,address)
        def forbidden(*args,**kwargs):raise AssertionError('EXTERNAL_FETCH_FORBIDDEN')
        try:
            with patch.object(socket.socket,'connect',local_only),patch.object(official,'fetch_json',forbidden),patch.object(app,'fetch_pulse',forbidden):
                for path in ['/','/app.js','/style.css','/api/state']:
                    with opener.open(base+path,timeout=5) as r:self.assertEqual(r.status,200)
                payload=json.dumps({'format':'json','content':json.dumps([fixture()])}).encode()
                req=urllib.request.Request(base+'/api/import',data=payload,headers={'Content-Type':'application/json'},method='POST')
                with patch.dict(os.environ,{'ARB_HUB_ALLOW_IMPORT':'0'}):
                    with self.assertRaises(urllib.error.HTTPError) as caught:opener.open(req,timeout=5)
                    self.assertEqual(caught.exception.code,403)
                with patch.dict(os.environ,{'ARB_HUB_ALLOW_IMPORT':'1'}):
                    with opener.open(req,timeout=5) as r:self.assertEqual(json.load(r)['imported'],1)
                self.assertEqual(app.load_rows()[0]['coin'],'TEST')
                with self.assertRaises(urllib.error.HTTPError) as caught:opener.open(base+'/../../app.py',timeout=5)
                self.assertEqual(caught.exception.code,404)
        finally:
            server.shutdown();server.server_close();thread.join()
            (app.IMPORTS/'manual.json').unlink(missing_ok=True)

if __name__=='__main__':unittest.main(verbosity=2)
