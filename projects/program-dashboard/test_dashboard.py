import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from http.server import ThreadingHTTPServer

import server
import collect_local

class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.runtime=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def test_01_service_running(self):
        result=type('R',(),{'returncode':0,'stdout':'LoadState=loaded\nActiveState=active\nSubState=running\n'})()
        with patch.object(server.subprocess,'run',return_value=result):
            self.assertEqual(server.service_status('system','fixture.service')['state'],'running')
    def test_02_service_failure(self):
        result=type('R',(),{'returncode':0,'stdout':'LoadState=loaded\nActiveState=failed\n'})()
        with patch.object(server.subprocess,'run',return_value=result):
            self.assertEqual(server.service_status('system','fixture.service')['state'],'error')
    def test_03_service_read_error_not_stopped(self):
        result=type('R',(),{'returncode':1,'stdout':''})()
        with patch.object(server.subprocess,'run',return_value=result):
            self.assertEqual(server.service_status('system','fixture.service')['state'],'unknown')
    def test_04_stale_local_status_not_current(self):
        data={'captured_at':(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),'items':[{'id':'binance-alpha','name':'测试程序','state':'running','private_key':'must-not-leak'}]}
        (self.runtime/'local_snapshot.json').write_text(json.dumps(data))
        d=server.Inventory(self.runtime,catalog=[{'id':'binance-alpha','name':'币安Alpha'}]).snapshot()
        self.assertEqual(d['local_sync']['state'],'stale')
        row=next(x for x in d['items'] if x['id']=='binance-alpha')
        self.assertEqual(row['state'],'unknown');self.assertNotIn('private_key',row)
    def test_05_missing_snapshot(self):
        d=server.Inventory(self.runtime,catalog=[]).snapshot()
        self.assertEqual(d['local_sync']['state'],'missing')
    def test_06_auth_readonly_and_assets(self):
        token='fixture-access-code'
        http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        http.token_hash=hashlib.sha256(token.encode()).hexdigest()
        http.inventory=server.Inventory(self.runtime,catalog=[])
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));base='http://127.0.0.1:'+str(http.server_port)
        try:
            for path in ['/','/api/status','/v/wrong/api/status','/v/'+token+'/../server.py']:
                with self.assertRaises(urllib.error.HTTPError) as caught:opener.open(base+path,timeout=4)
                self.assertEqual(caught.exception.code,404)
            for path in ['/health','/v/'+token+'/','/v/'+token+'/app.js','/v/'+token+'/style.css','/v/'+token+'/api/status']:
                with opener.open(base+path,timeout=4) as response:
                    self.assertEqual(response.status,200)
                    self.assertEqual(response.headers['Referrer-Policy'],'no-referrer')
                    self.assertEqual(response.headers['Cache-Control'],'no-store')
            for method in ['POST','PUT','DELETE','PATCH']:
                req=urllib.request.Request(base+'/v/'+token+'/api/status',data=b'{}',method=method)
                with self.assertRaises(urllib.error.HTTPError) as caught:opener.open(req,timeout=4)
                self.assertEqual(caught.exception.code,405)
        finally:http.shutdown();http.server_close();thread.join()
    def test_07_old_snapshot_cannot_reintroduce_non_github_tasks(self):
        data={'captured_at':datetime.now(timezone.utc).isoformat(),'items':[{'id':'old-task','name':'SHOULD_NOT_APPEAR','state':'running'},{'id':'binance-alpha','state':'scheduled','name':'DO_NOT_OVERRIDE_NAME','private_key':'must-not-leak'}]}
        (self.runtime/'local_snapshot.json').write_text(json.dumps(data))
        d=server.Inventory(self.runtime,catalog=[{'id':'binance-alpha','name':'币安Alpha'}]).snapshot()
        self.assertEqual(len(d['items']),1)
        self.assertEqual(d['items'][0]['name'],'币安Alpha')
        self.assertEqual(d['items'][0]['state'],'scheduled')
        self.assertNotIn('must-not-leak',json.dumps(d))
    def test_08_collector_only_reads_exact_github_bindings(self):
        with patch.object(collect_local.subprocess,'check_output',return_value='PID Status Label\n'):
            d=collect_local.collect(self.runtime)
        self.assertEqual(d['items'],[])
        self.assertEqual(set(collect_local.PROJECT_IDS.values()),{'binance-alpha','binance-booster'})
    def test_09_catalog_matches_all_github_project_directories(self):
        rows=json.loads((server.HERE/'catalog.json').read_text())
        slugs={row['code'].rsplit('/',1)[-1] for row in rows}
        actual={p.name for p in server.HERE.parent.iterdir() if p.is_dir() and (p/'README.md').is_file()}
        self.assertEqual(slugs,actual)
        self.assertEqual(len(rows),len({row['id'] for row in rows}))
        self.assertTrue(all(row['code'].startswith('https://github.com/q44820/ai-workspace/tree/main/projects/') for row in rows))
        self.assertNotIn('network-xray',{row['id'] for row in rows})

if __name__=='__main__':unittest.main(verbosity=2)
