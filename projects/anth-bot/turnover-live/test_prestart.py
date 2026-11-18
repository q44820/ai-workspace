"""Offline pre-start regressions. No production credentials or order endpoints."""
import asyncio,tempfile,time,unittest,sys,types
from pathlib import Path
from unittest.mock import AsyncMock,Mock,patch
import adapter,core

class Prestart(unittest.IsolatedAsyncioTestCase):
 def test_auth_uses_explicit_key_index_and_short_expiry(self):
  v=adapter.Venue();v.key_index=7;v.lt=Mock();v.lt.create_auth_token_with_expiry.return_value=('fixture-token',None)
  self.assertEqual(v.auth(),'fixture-token')
  v.lt.create_auth_token_with_expiry.assert_called_once_with(deadline=600,api_key_index=7)
 def test_shallow_book_is_no_candidate_not_session_failure(self):
  b={'io':{'bids':[[1700,.00001]],'asks':[[1700.1,.00001]]},'lt':{'bids':[[1700,.00001]],'asks':[[1700.1,.00001]]}}
  self.assertIsNone(core.entry(b,core.Config(),3,5))
 def test_hl_prices_obey_five_significant_figures_and_tick(self):
  self.assertEqual(adapter.hl_price(1700.1234,3,True,True),1700.1)
  self.assertEqual(adapter.hl_price(1700.1234,3,False,True),1700.2)
  self.assertEqual(adapter.hl_price(1700.1234,3,True,False),1700.2)
  self.assertEqual(adapter.hl_price(1700.1234,3,False,False),1700.1)
  self.assertEqual(adapter.hl_price(.0123456,1,True,True),.01234)
  self.assertEqual(adapter.hl_price(123456,3,True,True),123456)
 def test_tiny_round_budget_is_skipped_not_division_by_zero(self):
  b={'io':{'bids':[[1700,10]],'asks':[[1700.1,10]]},'lt':{'bids':[[1700,10]],'asks':[[1700.1,10]]}}
  self.assertIsNone(core.entry(b,core.Config(round_cap=.001),3,5))
 async def test_null_last_close_does_not_crash_after_warmup(self):
  from test_live import ControllerFixture
  with tempfile.TemporaryDirectory() as t,patch('core.other_service_active',return_value=False):
   v=ControllerFixture();c=core.Controller(Path(t)/'s',lambda:v);c.venue=v;c.state.update(started=time.time()-100,lastClose=None);c.running=True
   original=c.refresh;n=0;clock=[time.time()]
   async def refresh():
    nonlocal n;n+=1;clock[0]+=10
    b=await original()
    if n>=11:c.stop.set()
    return b
   c.refresh=refresh
   with patch('core.time.time',side_effect=lambda:clock[0]),patch('core.asyncio.sleep',new=AsyncMock()):await c.run()
   self.assertEqual(c.state['phase'],'finished');self.assertNotEqual(c.state.get('lastError',{}).get('type'),'TypeError')
 async def test_rejection_is_terminal_without_fill_reads(self):
  v=adapter.Venue();v.order=AsyncMock(side_effect=AssertionError('must not query'))
  self.assertEqual(await v.settle({},dict(rejected=True),asyncio.Event()),dict(terminal=True,fills=[]))
 async def test_partial_maker_cancel_once_then_wait_trade_evidence(self):
  v=adapter.Venue();v.hl=Mock();v.hl.cancel_by_cloid=Mock(return_value={'status':'ok'})
  i=dict(venue='io',client='1'*32,qty=.01,created=time.time(),side=1)
  v.order=AsyncMock(side_effect=[dict(terminal=False,filled=.003,id=12),dict(terminal=True,filled=.003,id=12),dict(terminal=True,filled=.003,id=12)])
  f=dict(venue='io',id='fixture',qty=.003,price=1700,side=1,fee=.001,pnl=0)
  v.fills=AsyncMock(side_effect=[[],[f]])
  cloid=types.ModuleType('hyperliquid.utils.types');setattr(cloid,'Cloid',types.SimpleNamespace(from_str=lambda x:x))
  with patch.dict(sys.modules,{'hyperliquid':types.ModuleType('hyperliquid'),'hyperliquid.utils':types.ModuleType('hyperliquid.utils'),'hyperliquid.utils.types':cloid}),patch('adapter.asyncio.sleep',new=AsyncMock()):r=await v.settle(i,dict(maker=True),asyncio.Event())
  self.assertEqual(r['fills'],[f]);self.assertEqual(v.hl.cancel_by_cloid.call_count,1)
 async def test_lt_order_exact_identity_and_terminal(self):
  class D:
   def to_dict(self):return {'code':200,'orders':[dict(client_order_index=9,owner_account_index=7,market_index=193,is_ask=True,reduce_only=True,initial_base_amount='.01',filled_base_amount='.004',status='canceled',order_index=12)]}
  v=adapter.Venue();v.index=7;v.auth=lambda:'fixture';v.lt=Mock();v.lt.order_api.account_orders=AsyncMock(return_value=D())
  i=dict(venue='lt',client=9,side=-1,reduce=True,qty=.01)
  self.assertEqual(await v.order(i),dict(state='canceled',id=12,filled=.004,terminal=True))
  i['reduce']=False
  with self.assertRaises(core.Blocked):await v.order(i)
 async def test_unknown_settlement_never_auto_resubmits(self):
  with tempfile.TemporaryDirectory() as t,patch('core.other_service_active',return_value=False):
   v=Mock();v.submit=AsyncMock(return_value={'maker':False});v.settle=AsyncMock(side_effect=TimeoutError('fixture'))
   c=core.Controller(Path(t)/'s',lambda:v);c.venue=v
   for _ in range(2):
    with self.assertRaises(core.Blocked):await c.execute('lt',1,.01,1700,'open')
   self.assertEqual(v.submit.await_count,1);self.assertEqual(c.state['journal'][0]['outcome'],'unknown')
 async def test_lt_exact_quantity_never_silently_rounded(self):
  v=adapter.Venue();v.lt=Mock();v.lt.create_market_order=AsyncMock();v.key_index=7
  i=dict(venue='lt',side=1,qty=.000001,price=1700,client=9,reduce=False)
  with self.assertRaises(core.Blocked):await v.submit(i)
  self.assertEqual(v.lt.create_market_order.await_count,0)
 async def test_lt_submit_exact_size_and_reduce_only(self):
  v=adapter.Venue();v.lt=Mock();v.lt.create_market_order=AsyncMock(return_value=(None,types.SimpleNamespace(code=200),None))
  r=await v.submit(dict(venue='lt',side=-1,qty=.014,price=1700,client=9,reduce=True))
  self.assertFalse(r['terminal']);kw=v.lt.create_market_order.call_args.kwargs
  self.assertEqual(kw['base_amount'],1400);self.assertTrue(kw['is_ask']);self.assertTrue(kw['reduce_only'])
  # Leave key/nonce rotation to SDK order nonce manager; auth alone needs explicit key.
  self.assertNotIn('api_key_index',kw);self.assertNotIn('nonce',kw)
 async def test_lt_actual_fill_fee_and_identity(self):
  trade=dict(trade_id=42,ask_id=12,bid_id=99,market_id=193,ask_account_id=7,ask_client_id=9,is_maker_ask=False,size='.014',price='1700',taker_fee=280,integrator_taker_fee=0,ask_account_pnl='.01')
  class D:
   def to_dict(self):return dict(code=200,trades=[trade],next_cursor='')
  v=adapter.Venue();v.index=7;v.auth=lambda:'fixture';v.lt=Mock();v.lt.order_api.trades=AsyncMock(return_value=D())
  i=dict(venue='lt',side=-1,client=9)
  f=(await v.fills(i,12))[0];self.assertAlmostEqual(f['fee'],.006664);self.assertEqual(f['qty'],.014)
  trade['ask_account_id']=8
  with self.assertRaises(core.Blocked):await v.fills(i,12)
 async def test_unknown_ioc_terminal_never_assumed_filled(self):
  v=adapter.Venue();v.order=AsyncMock(return_value=None);v.fills=AsyncMock()
  with patch('adapter.time.monotonic',side_effect=[0,46]):
   with self.assertRaises(core.Blocked):await v.settle(dict(venue='lt'),dict(maker=False),asyncio.Event())
  self.assertEqual(v.fills.await_count,0)
 async def test_zero_fee_omission_requires_exact_aggregate_evidence(self):
  trade=dict(trade_id=42,ask_id=99,bid_id=12,market_id=193,bid_account_id=7,bid_client_id=9,is_maker_ask=True,size='.014',price='1711.4',taker_fee=None,integrator_taker_fee=None,bid_account_pnl=None)
  class D:
   def to_dict(self):return dict(code=200,trades=[trade],next_cursor='')
  aggregate=dict(account_index=7,order_id='12',market_id=193,is_ask=False,type='trade',role='taker',size='.014',usd_amount='23.9596',fee='0',integrator_fee='0',fee_asset_id=3,pnl=None,fill_count=1,first_trade_id='42',last_trade_id='42')
  v=adapter.Venue();v.index=7;v.auth=lambda:'fixture';v.lt=Mock();v.lt.order_api.trades=AsyncMock(return_value=D());v.get=AsyncMock(return_value=dict(code=200,trades=[aggregate],next_cursor=''))
  i=dict(venue='lt',side=1,client=9,reduce=False)
  f=await v.fills(i,12);self.assertEqual(len(f),1);self.assertEqual(f[0]['fee'],0);self.assertEqual(f[0]['pnl'],0);self.assertEqual(f[0]['qty'],.014)
  aggregate['size']='.013'
  with self.assertRaises(core.Blocked):await v.fills(i,12)
  aggregate['size']='.014';aggregate['account_index']=8
  with self.assertRaises(core.Blocked):await v.fills(i,12)
  aggregate['account_index']=7;i['reduce']=True
  with self.assertRaises(core.Blocked):await v.fills(i,12)

if __name__=='__main__':unittest.main()
