"""Offline fixtures only: no SDK initialization, signing, account writes or real orders."""
import asyncio,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import core

class HTTP(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  import importlib.util
  from aiohttp.test_utils import TestClient,TestServer
  spec=importlib.util.spec_from_file_location('turnover_api',Path(__file__).with_name('server.py'));api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)
  self.tmp=tempfile.TemporaryDirectory();self.factory_calls=[]
  def factory():self.factory_calls.append(1);raise AssertionError('must not initialize')
  self.client=TestClient(TestServer(api.create_app(Path(self.tmp.name)/'state.json','x'*40,factory)));await self.client.start_server();self.headers={'Authorization':'Bearer '+'x'*40}
 async def asyncTearDown(self):await self.client.close();self.tmp.cleanup()
 async def test_get_and_unauthorized_never_initialize_sdk(self):
  self.assertEqual((await self.client.get('/state')).status,403)
  with patch('core.other_service_active',return_value=True):
   r=await self.client.get('/state',headers=self.headers);d=await r.json()
  self.assertFalse(d['running']);self.assertEqual(d['config']['target'],1000);self.assertEqual(self.factory_calls,[])
 async def test_csrf_and_arb_conflict(self):
  r=await self.client.get('/state',headers=self.headers);d=await r.json();body={'confirm':'用户手动启动本次真实合约小额试验','config':{}}
  self.assertEqual((await self.client.post('/start',headers=self.headers,json=body)).status,403)
  with patch('core.other_service_active',return_value=True):
   r=await self.client.post('/start',headers={**self.headers,'X-Turnover-CSRF':d['csrf']},json=body);self.assertEqual(r.status,409)
  self.assertEqual(self.factory_calls,[])


class Rules(unittest.TestCase):
 def test_defaults(self):
  c=core.Config();self.assertEqual((c.target,c.round_cap,c.loss),(1000,50,3))
 def test_reject_nonfinite_and_expansion(self):
  for k,v in [('target',1001),('round_cap',51),('loss',4),('loss',float('nan')),('target',True),('other',1)]:
   with self.assertRaises(ValueError):core.Config.parse({k:v})
 def test_round_is_two_legs_not_one(self):
  c=core.Config();q=core.size_for(c,1700,1710,3,3)
  self.assertLessEqual(q*(1700+1710)*1.003,c.round_cap);self.assertGreaterEqual(q*1700,10)
 def test_min_notional_rejects_tiny(self):
  with self.assertRaises(ValueError):core.size_for(core.Config.parse({'round_cap':5}),1700,1700,3,3)
 def test_ledger_dedup_and_actual_notional(self):
  l=core.Ledger();f=dict(venue='io',id='1',side=1,qty=.01,price=1700,fee=.002,pnl=0)
  l.add(f);l.add(f);self.assertEqual(l.total,17);self.assertEqual(l.net,-.002)
  l.add(dict(f,venue='lt',fee=.003));self.assertEqual(l.total,34);self.assertEqual(l.net,-.005)
 def test_missing_fee_never_estimated_as_zero(self):
  with self.assertRaises(core.Blocked):core.Ledger().add(dict(venue='io',id='1',side=1,qty=.1,price=10,fee=None,pnl=0))
 def test_unknown_pnl_is_not_realized_profit(self):
  with self.assertRaises(core.Blocked):core.Ledger().add(dict(venue='lt',id='1',side=1,qty=.1,price=10,fee=0,pnl=None))
 def test_restart_never_resumes(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'state.json';p.write_text(json.dumps({'phase':'running','journal':[{'venue':'io','outcome':'unknown'}],'fills':[],'config':{}}));c=core.Controller(p,None)
   self.assertFalse(c.running);self.assertEqual(c.state['phase'],'needs_review');self.assertEqual(c.state['journal'][0]['outcome'],'unknown')
 def test_turnover_candidate_and_exit(self):
  b={'io':{'bids':[[1700,2]],'asks':[[1700.1,2]]},'lt':{'bids':[[1700,2]],'asks':[[1700.1,2]]}}
  a=core.entry(b,core.Config(),3,3);self.assertIn(a['dir'],['S','L']);self.assertLessEqual(a['qty']*(a['price']+1700.1)*1.003,50)
  p=dict(dir='S',qty=.01,io=1700.1,lt=1700.1,opened=time.time(),fees=.005)
  x=core.exit_price(b,p,False);y=core.exit_price(b,p,True);self.assertLessEqual(x,y)
 def test_guard_volume_and_loss_include_float_and_funding(self):
  self.assertEqual(core.stop_reason(core.Config(),1000,0),'volume')
  self.assertEqual(core.stop_reason(core.Config(),0,-3),'loss')
  self.assertIsNone(core.stop_reason(core.Config(),999,-2.99))
 def test_no_credential_fields_in_public_snapshot(self):
  with tempfile.TemporaryDirectory() as t:
   c=core.Controller(Path(t)/'s.json',None);self.assertNotIn('credentials',c.public());self.assertFalse(c.public()['running'])

class Flow(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.service=patch('core.other_service_active',return_value=False);self.service.start();self.addCleanup(self.service.stop)
 async def test_partial_fill_hedges_only_confirmed_quantity(self):
  with tempfile.TemporaryDirectory() as t:
   v=Fixture();c=core.Controller(Path(t)/'s.json',lambda:v);c.venue=v;c.config=core.Config();c.state['session']='fixture';c.state['journal']=[]
   await c.execute('io',1,.01,1700,'open',maker=True)
   self.assertEqual(v.sent[0]['qty'],.01);self.assertEqual(c.ledger.total,8.5);self.assertEqual(c.state['journal'][0]['outcome'],'terminal')
 async def test_ambiguous_submit_no_retry(self):
  with tempfile.TemporaryDirectory() as t:
   v=Fixture();v.ambiguous=True;c=core.Controller(Path(t)/'s.json',lambda:v);c.venue=v;c.config=core.Config();c.state['session']='fixture'
   with self.assertRaises(core.Blocked):await c.execute('io',1,.01,1700,'open',maker=True)
   self.assertEqual(len(v.sent),1);self.assertEqual(c.state['journal'][0]['outcome'],'unknown')
   with self.assertRaises(core.Blocked):await c.execute('io',1,.01,1700,'open',maker=True)
   self.assertEqual(len(v.sent),1)
 async def test_preflight_busy_does_not_initialize_signer(self):
  with tempfile.TemporaryDirectory() as t:
   c=core.Controller(Path(t)/'s.json',lambda:(_ for _ in ()).throw(AssertionError('signer initialized')))
   with patch('core.other_service_active',return_value=True):
    with self.assertRaises(core.Blocked):await c.start({})
   self.assertFalse(c.running)

class Fixture:
 def __init__(self):self.sent=[];self.ambiguous=False
 async def submit(self,i,maker=False):
  self.sent.append(dict(i))
  if self.ambiguous:raise TimeoutError('fixture ambiguous')
  return {'id':'fixture-id','filled':.005,'terminal':False}
 async def settle(self,i,ack,stop):return {'terminal':True,'fills':[dict(venue=i['venue'],id='fixture-fill',side=i['side'],qty=.005,price=1700,fee=.002,pnl=0)]}

class ControllerFixture:
 io_dec=3;lt_dec=3
 def __init__(self):self.sent=[];self.n=0;self.closed=False
 async def initialize(self):pass
 async def preflight(self):pass
 async def close(self):self.closed=True
 async def no_orders(self):pass
 async def book(self):return {'at':__import__('time').time(),'io':{'bids':[[1700,2]],'asks':[[1700.1,2]]},'lt':{'bids':[[1700,2]],'asks':[[1700.1,2]]}}
 async def funding(self,since):return 0
 async def exposure(self):
  return {k:round(sum(i['side']*i['filled'] for i in self.sent if i['venue']==k),9) for k in ['io','lt']}
 async def submit(self,i,maker=False):
  self.sent.append({**i,'filled':i['qty']/2 if i['venue']=='io' else i['qty']});return {'id':'fixture','terminal':True}
 async def settle(self,i,ack,stop):
  self.n+=1;return {'terminal':True,'fills':[dict(venue=i['venue'],id='fixture-'+str(self.n),side=i['side'],qty=self.sent[-1]['filled'],price=1700,fee=.001,pnl=0)]}

class PairedFlow(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.s=patch('core.other_service_active',return_value=False);self.s.start();self.addCleanup(self.s.stop)
  self.v=ControllerFixture();self.c=core.Controller(Path(self.t.name)/'s.json',lambda:self.v);self.c.venue=self.v;self.c.state.update(session='offline-fixture',started=__import__('time').time())
 async def test_open_hedge_exact_partial_then_partial_close_and_emergency(self):
  b=await self.v.book();a=core.entry(b,self.c.config,3,3);self.assertIsNotNone(a)
  await self.c.pair(b,a);self.assertEqual(self.v.sent[1]['qty'],self.v.sent[0]['qty']/2);self.assertFalse(self.c.position['unhedged'])
  q=self.c.position['qty'];await self.c.pair(b,None,True,True);self.assertAlmostEqual(self.c.position['qty'],q/2);self.assertTrue(self.v.sent[-1]['reduce'])
  # Fixture stops partially filling IO for deterministic emergency-flat evidence.
  async def full(i,maker=False):self.v.sent.append({**i,'filled':i['qty']});return {'id':'fixture','terminal':True}
  self.v.submit=full;self.assertTrue(await self.c.emergency_reduce());self.assertIsNone(self.c.position);self.assertEqual(await self.v.exposure(),{'io':0,'lt':0})
 async def test_stop_requested_flat_never_opens(self):
  self.c.running=True;self.c.stop.set();await self.c.run();self.assertEqual(self.v.sent,[]);self.assertFalse(self.c.running);self.assertEqual(self.c.state['phase'],'finished')
 async def test_volume_reached_only_drains_without_new_open(self):
  # Account quantities balanced zero; fills explicitly synthetic offline history.
  for side in [1,-1]:self.c.ledger.add(dict(venue='io',id='fixture-history'+str(side),side=side,qty=1,price=600,fee=0,pnl=0))
  self.c.running=True;await self.c.run();self.assertEqual(self.v.sent,[]);self.assertEqual(self.c.state['phase'],'finished')
 async def test_delayed_replica_retries_reads_not_orders(self):
  from unittest.mock import AsyncMock
  self.c.ledger.add(dict(venue='io',id='fixture-lag',side=1,qty=.01,price=1700,fee=.001,pnl=0))
  self.v.exposure=AsyncMock(side_effect=[{'io':0,'lt':0},{'io':.01,'lt':0}]);await self.c.verify();self.assertEqual(self.v.exposure.await_count,2);self.assertEqual(self.v.sent,[])
 async def test_exception_records_sanitized_origin(self):
  self.c.running=True
  async def broken():raise RuntimeError('SECRET fixture payload')
  self.c.venue.book=broken;await self.c.run()
  error=self.c.state.get('lastError',{});self.assertEqual(error.get('type'),'RuntimeError');self.assertEqual(error.get('stage'),'market')
  self.assertNotIn('SECRET',json.dumps(self.c.public()));self.assertTrue(error.get('frames'))
 async def test_transient_read_failure_does_not_freeze_flat_session(self):
  from unittest.mock import AsyncMock
  b=await self.v.book();self.v.book=AsyncMock(side_effect=[TimeoutError('fixture'),b]);self.c.running=True;self.c.state['lastFunding']=time.time();await self.c.refresh()
  self.assertEqual(self.v.book.await_count,2);self.assertFalse(self.c.state['phase']=='needs_review');self.assertEqual(self.v.sent,[])
 async def test_restart_unknown_cannot_clear_gate(self):
  self.c.state.update(session=None,phase='needs_review',journal=[])
  with self.assertRaises(core.Blocked):await self.c.start({})
  self.assertEqual(self.v.sent,[])

if __name__=='__main__':unittest.main()
