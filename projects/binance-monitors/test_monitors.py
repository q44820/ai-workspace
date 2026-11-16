"""Synthetic offline tests; no public APIs, notifications or live state."""
import contextlib
import copy
import importlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

alpha = importlib.import_module('binance_alpha_monitor')
booster = importlib.import_module('binance_booster_monitor')
runtime = importlib.import_module('telegram_runtime')

class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for module in (alpha, booster):
            for name, value in [('RUNTIME', Path(self.tmp.name)), ('STATE_PATH', Path(self.tmp.name) / (module.__name__ + '.json')), ('LOG_PATH', Path(self.tmp.name) / (module.__name__ + '.log')), ('PUSH', False)]:
                p = patch.object(module, name, value);p.start();self.addCleanup(p.stop)
        self.net = patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden'));self.net.start();self.addCleanup(self.net.stop)
    def test_01_default_push_disabled(self):
        import subprocess
        import sys
        env = {k:v for k,v in os.environ.items() if k not in ('ALPHA_MONITOR_PUSH','BOOSTER_MONITOR_PUSH')}
        result = subprocess.check_output([sys.executable,'-c','import binance_alpha_monitor as a, binance_booster_monitor as b; assert not a.PUSH and not b.PUSH'],env=env,cwd=Path(__file__).parent)
        self.assertEqual(result,b'')
    def test_02_stable_airdrop_key(self):
        self.assertEqual(alpha.airdrop_key({'token':'demo','date':'2030-01-01'}), 'token:DEMO:2030-01-01')
    def test_03_contract_key(self):
        self.assertEqual(alpha.airdrop_key({'contract_address':'0xAbC'}), 'addr:0xabc')
    def test_04_share_count(self):
        self.assertEqual(alpha.share_count_value({'amount':'10','total_amount':'1,000'}), 100)
        self.assertIsNone(alpha.share_count_value({'amount':'0','total_amount':'10'}))
    def test_05_timezone(self):
        self.assertEqual(alpha.parse_scheduled_dt({'date':'2030-01-01','time':'08:00'}).hour, 0)
        self.assertEqual(alpha.parse_scheduled_dt({'date':'2030-01-01','utc':'09:00','time':'08:00'}).hour, 9)
    def test_06_diff(self):
        self.assertEqual(alpha.diff_item({'points':100},{'points':100}), [])
        self.assertTrue(alpha.diff_item({'points':100},{'points':200}))
    def test_07_alpha_main_local_state(self):
        sample={'token':'DEMO','date':'2030-01-01','points':100,'amount':'10'}
        with patch.object(alpha,'fetch_api',return_value={'airdrops':[sample]}), patch.object(alpha,'market_circulation_check',return_value={}), patch.object(alpha,'monitor_stability',return_value=0), patch.object(alpha.time,'sleep'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(alpha.main(),0)
        self.assertEqual(len(alpha.load_state()['airdrops']),1)
    def test_08_alpha_cap(self):
        sample={'token':'DEMO','date':'2030-01-01','points':200}
        key=alpha.airdrop_key(sample);alpha.save_state({'airdrops':{key:{**sample,'points':100}},'alerts':{key:{'count':3}}})
        with patch.object(alpha,'fetch_api',return_value={'airdrops':[sample]}),patch.object(alpha,'market_circulation_check',return_value={}),patch.object(alpha,'monitor_stability',return_value=0),patch.object(alpha,'dispatch') as dispatch,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(alpha.main(),0)
        dispatch.assert_not_called()
    def test_09_booster_relevance(self):
        with patch.object(booster,'MONITOR_ALL',False):
            self.assertTrue(booster.is_relevant('Binance Wallet Booster')[0])
            self.assertFalse(booster.is_relevant('unrelated text')[0])
        with patch.object(booster,'MONITOR_ALL',True):self.assertTrue(booster.is_relevant('general announcement')[0])
    def test_10_body_parser(self):
        self.assertEqual(booster.body_to_text({'children':[{'text':'hello'},{'text':'world'}]}),'hello world')
    def test_11_first_run_baseline(self):
        item={'key':'article:synthetic','title':'Synthetic fixture'}
        with patch.object(booster,'probe_booster_page',return_value={'ok':False}),patch.object(booster,'fetch_candidates',return_value=[item]),patch.object(booster,'NOTIFY_INITIAL',False),patch.object(booster,'dispatch_channels') as send,contextlib.redirect_stdout(io.StringIO()):
            booster.main();booster.main()
        send.assert_not_called();self.assertEqual(len(booster.load_state()['seen']),1)
    def test_12_channel_retry_only_pending(self):
        booster.save_state({'seen':{'old':{}},'sent_channels':{'article:new':['telegram']}})
        with patch.object(booster,'PUSH',True),patch.object(booster,'CHANNEL','both'),patch.object(booster,'probe_booster_page',return_value={'ok':False}),patch.object(booster,'fetch_candidates',return_value=[{'key':'article:new'}]),patch.object(booster,'dispatch_channels',return_value={'wxpusher':True}) as send,contextlib.redirect_stdout(io.StringIO()):booster.main()
        self.assertEqual(send.call_args.args[1],['wxpusher']);self.assertIn('article:new',booster.load_state()['seen'])
    def test_13_env_only_credentials(self):
        with patch.dict(os.environ,{},clear=True):self.assertEqual(runtime.load_telegram_runtime(),('',''))
    def test_14_redaction(self):
        with patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'synthetic-secret'}):self.assertNotIn('synthetic-secret',runtime.redact_log('hello synthetic-secret'))
    def test_15_stability_cooldown(self):
        state={};feed={'items':[{'n':'DEMO/U','st':'green:ok','spr':0.1}]}
        with patch.object(alpha,'fetch_stability_feed',return_value=feed),patch.object(alpha,'dispatch',return_value=True) as send,patch.object(alpha.time,'sleep'):
            alpha.monitor_stability(state);alpha.monitor_stability(state)
        self.assertEqual(send.call_count,1)

if __name__=='__main__':unittest.main()
