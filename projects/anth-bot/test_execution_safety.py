"""Offline execution regressions: fake venues, blocked network, temporary state."""
import os,sys,tempfile,socket,asyncio,unittest,json
from pathlib import Path
from unittest.mock import patch,AsyncMock
TMP=tempfile.TemporaryDirectory(prefix='anth-execution-tests-',dir=os.environ.get('TMPDIR'))
os.environ.update(DRY_RUN='1',LIVE_ENABLED='0',STATE_FILE=str(Path(TMP.name)/'state.json'),LOG_FILE=str(Path(TMP.name)/'test.log'),PUBLIC_PATH='',STATUS_PUSH_URL='',HL_ACCOUNT_ADDRESS='',LIGHTER_ACCOUNT_INDEX='0')
def deny(*a,**k):raise AssertionError('NETWORK_FORBIDDEN')
socket.socket.connect=deny;socket.create_connection=deny;socket.getaddrinfo=deny
import bot
REAL_SLEEP=asyncio.sleep
class ExecutionTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  bot.DRY_RUN=False;bot.LEVERAGE=2;Path(bot.STATE_F).unlink(missing_ok=True);bot.S=bot.State();s=bot.S;s.pos={'live':True,'dir':'S','qIo':.174,'qLt':.174,'dryIo':0,'dryLt':0};s.mode='live';s.intents=[];s.recovery=False;s.exit_pending=None;s.alert=None;s.actual=None;s.cfg['retries']=0
  now=bot.now_ms();bot.observe_loss_sample(100,100,now,now,0.0,now)
  self.v=bot.Venues();bot.V=self.v;self.v.ready=True;self.v.hl_addr='test-account';self.v.lt_idx=123
  self.q=[0.,0.];self.sent=[];self.first='full';self.second='full';self.block_after_first=False
  self.v.positions=AsyncMock(side_effect=self.positions);self.v.lookup_order=AsyncMock(side_effect=self.lookup)
  self.v.ack_timeout=0.05;self.v.ack_interval=0.005
  self.v.order_io=AsyncMock(side_effect=lambda *a:None)
  async def io(*a):return await self.send('io',*a)
  async def lt(*a):return await self.send('lt',*a)
  self.v.order_io=io;self.v.order_lt=lt
  self.books()
 def books(self):
  t=bot.now_ms();bot.B.io={'bids':[(1700.,5.)],'asks':[(1700.1,5.)],'t':t};bot.B.lt_b={'1660':5.};bot.B.lt_a={'1660.1':5.};bot.B.lt_t=t
 async def positions(self):return (*self.q,1700.,1660.)
 async def lookup(self,i):
  filled=i.get('_fake_filled',0.);state='filled' if filled==i['qty'] else 'canceled'
  if i['venue']=='io':return {'status':'order','order':{'status':state,'order':{'coin':i['market'],'side':'B' if i['direction']=='buy' else 'A','oid':1001,'cloid':i['official_id'],'reduceOnly':i['reduce'],'origSz':str(i['qty']),'sz':str(i['qty']-filled)}}}
  return {'code':200,'orders':[{'client_order_index':i['official_id'],'order_index':1002,'owner_account_index':i['account'],'market_index':i['market'],'is_ask':i['direction']=='sell','reduce_only':i['reduce'],'initial_base_amount':str(i['qty']),'filled_base_amount':str(filled),'remaining_base_amount':str(i['qty']-filled),'status':state}]}
 async def send(self,leg,qty,buy,px,slip,reduce):
  qty=abs(qty);i=self.v._require_intent(leg,qty,buy,reduce);self.sent.append((leg,qty,buy,reduce));action=self.first if leg=='io' and not reduce else (self.second if not reduce else 'full')
  if action=='timeout':
   self.v.lookup_order=AsyncMock(return_value={})
   raise asyncio.TimeoutError()
  if action=='reject':return {'response':{'data':{'statuses':[{'error':'IOC could not fill'}]}}} if leg=='io' else {'rejected':True}
  if action=='ioc_miss':return {'status':'ok','response':{'type':'order','data':{'statuses':[{'error':'Order could not immediately match against any resting orders. asset=110000'}]}}}
  if action=='margin_reject':return {'status':'ok','response':{'type':'order','data':{'statuses':[{'error':'Insufficient margin to place order. asset=110000'}]}}}
  filled=qty/2 if action=='partial' else qty;i['_fake_filled']=filled;self.q[0 if leg=='io' else 1]+=filled*(1 if buy else -1)
  if self.block_after_first and leg=='io':bot.B.lt_t=0
  if action=='unknown':self.v.lookup_order=AsyncMock(return_value={})
  return {'accepted':True}
 async def drive(self):return await self.v.drive_to(-.174,.174,False,'开仓')
 async def test_first_rejection_never_opens_second(self):
  self.first='reject';self.assertFalse(await self.drive());self.assertEqual([x[0] for x in self.sent],['io']);self.assertEqual(self.q,[0.,0.]);self.assertFalse(bot.unknown_orders())
 async def test_full_fill_is_confirmed_before_second(self):
  self.assertTrue(await self.drive());self.assertEqual(len(self.sent),2);self.assertEqual(bot.S.intents[1]['pre'],{'io':-.174,'lt':0.});self.assertFalse(bot.unknown_orders())
 async def test_partial_first_never_opens_unmatched_second(self):
  self.first='partial';self.assertFalse(await self.drive());self.assertEqual(len(self.sent),1);self.assertAlmostEqual(self.q[0],-.087);self.assertFalse(bot.unknown_orders())
 async def test_unknown_first_blocks_second_and_retries(self):
  self.first='unknown';self.assertFalse(await self.drive());self.assertEqual(len(self.sent),1);self.assertTrue(bot.unknown_orders());self.assertFalse(await self.drive());self.assertEqual(len(self.sent),1)
 async def test_timeout_blocks_second_and_retries(self):
  self.first='timeout'
  try:await self.drive()
  except asyncio.TimeoutError:pass
  self.assertEqual(len(self.sent),1);self.assertTrue(bot.unknown_orders());await self.drive();self.assertEqual(len(self.sent),1)
 async def test_second_rejected_rescue_only_reduces_first(self):
  self.second='reject';self.assertFalse(await self.drive());self.assertEqual(len(self.sent),2);self.assertTrue(await self.v.drive_to(0,0,True,'残腿全平'));self.assertEqual(self.sent[-1],('io',.174,True,True));self.assertEqual(self.q,[0.,0.])
 async def test_partial_second_cleanup_uses_actual_sizes(self):
  self.second='partial';self.assertFalse(await self.drive());self.assertTrue(await self.v.drive_to(0,0,True,'残腿全平'));self.assertEqual(self.sent[-2:], [('io',.174,True,True),('lt',.087,False,True)]);self.assertEqual(self.q,[0.,0.])
 async def test_stale_second_book_cannot_submit(self):
  self.block_after_first=True;self.assertFalse(await self.drive());self.assertEqual(len(self.sent),1)
 async def test_restart_unknown_intent_blocks_new_opening(self):
  self.first='unknown';await self.drive();bot.S=bot.State();bot.S.pos['opening_pending']=True;self.v._lookup_at.clear();await self.drive();self.assertEqual(len(self.sent),1)
 async def test_no_position_does_not_bypass_recovery(self):
  bot.S.pos=None;self.assertFalse(await self.drive());self.assertTrue(bot.S.recovery);self.assertEqual(self.sent,[])
 async def test_unknown_exposure_never_sends(self):
  self.v.positions=AsyncMock(return_value=(None,None,None,None));self.assertFalse(await self.drive());self.assertEqual(self.sent,[])
 async def test_flat_exit_updates_stale_alert_without_resuming(self):
  bot.S.mode='stopped';bot.S.actual={'io':0.,'lt':0.,'t':bot.now_ms()};bot.S.exit_pending={'submitted':True};bot.S.alert='⚠️ 平仓未完成，已停止机器人，请手动检查两边持仓'
  bot._finalize_flat({'live':True,'reason':'残腿全平','u':None,'status':'unreconciled'},bot.now_ms());self.assertIsNone(bot.S.pos);self.assertNotIn('平仓未完成',bot.S.alert or '');self.assertEqual(bot.S.mode,'stopped')
 async def test_nonzero_exposure_keeps_local_position(self):
  bot.S.actual={'io':-.174,'lt':0.,'t':bot.now_ms()};bot._finalize_flat({'live':True,'reason':'残腿全平','u':None},bot.now_ms());self.assertIsNotNone(bot.S.pos)
 async def test_stale_actual_after_fill_blocks_second(self):
  self.v.positions=AsyncMock(return_value=(0.,0.,1700.,1660.));self.assertFalse(await self.drive());self.assertEqual(len(self.sent),1);self.assertTrue(bot.unknown_orders())
 async def test_second_depth_insufficient_prevents_first_send(self):
  bot.B.lt_a={'1660.1':.01};self.assertFalse(await self.drive());self.assertEqual(self.sent,[])
 async def test_rejected_round_cannot_reopen(self):
  self.first='reject';await self.drive();self.first='full';await self.drive();self.assertEqual(len(self.sent),1)
 async def test_reduce_flow_sends_both_exits_before_reconcile(self):
  self.q[:]=[-.174,.174];self.assertTrue(await self.v.drive_to(0,0,True,'平仓'));self.assertEqual([x[0] for x in self.sent],['io','lt']);self.assertTrue(all(x[3] for x in self.sent))
 async def test_delayed_partial_reconcile_becomes_rescue_not_normal_hold(self):
  self.first='unknown';await self.drive();self.v.lookup_order=self.lookup;self.v._lookup_at.clear();bot.S.pos.update(t=bot.now_ms()-1000,spread=0.,opening_pending=True);await bot.reconcile_exit(bot.now_ms());self.assertTrue(bot.S.pos['rescue_pending']);self.assertEqual(bot.S.pos['close_requested'],'残腿全平');self.assertEqual(bot.S.mode,'stopped');self.assertEqual(len(self.sent),1)
 async def test_delayed_flat_clears_alert_stays_stopped(self):
  bot.S.mode='stopped';bot.S.pos.update(t=bot.now_ms()-1000,spread=0.);bot.S.exit_pending={'submitted':True,'why':'残腿全平'};bot.S.alert='⚠️ 平仓未完成，已停止机器人，请手动检查两边持仓';await bot.reconcile_exit(bot.now_ms());self.assertIsNone(bot.S.pos);self.assertNotIn('平仓未完成',bot.S.alert or '');self.assertEqual(bot.S.mode,'stopped')
 async def test_normal_close_waits_for_terminal_receipts_then_stays_live(self):
  p=bot.S.pos; assert p is not None
  p.update(t=bot.now_ms()-1000,spread=0.,N=290.); self.q[:]=[-.174,.174]
  original_drive=self.v.drive_to
  async def delayed(*a,**k):
   result=await original_drive(*a,**k)
   self.v._lookup_at.clear()
   return result
  async def missing_receipt(i): return {}
  self.v.lookup_order=missing_receipt
  self.v.drive_to=delayed
  self.v.fills=AsyncMock(side_effect=TimeoutError('optional fills delayed'))
  await bot.close_round(bot.now_ms(),0.,'回归中位数')
  self.assertEqual(bot.S.mode,'live');self.assertIsNotNone(bot.S.pos);assert bot.S.exit_pending is not None
  self.assertTrue(bot.S.exit_pending['submitted'])
  sent=len(self.sent);self.assertGreater(sent,0)
  await bot.close_round(bot.now_ms(),0.,'回归中位数');self.assertEqual(len(self.sent),sent)
  self.v.lookup_order=self.lookup; self.v._lookup_at.clear(); self.v._exposure_at=None
  await bot.reconcile_exit(bot.now_ms())
  self.assertIsNone(bot.S.pos);self.assertIsNone(bot.S.exit_pending);self.assertEqual(bot.S.mode,'live');self.assertGreater(bot.S.cool,bot.now_ms());self.assertEqual(len(self.sent),sent);self.assertIsNone(bot.S.alert)
 async def test_missing_optional_fills_does_not_prevent_confirmed_flat(self):
  p=bot.S.pos; assert p is not None
  p.update(t=bot.now_ms()-1000,spread=0.,N=290.,pxIo=1700.,pxLt=1660.); self.q[:]=[-.174,.174]
  self.v.fills=AsyncMock(side_effect=TimeoutError('optional fills delayed'))
  with patch.object(bot.asyncio,'sleep',new=AsyncMock()):
   await bot.close_round(bot.now_ms(),0.,'回归中位数')
  self.assertEqual(bot.S.mode,'live');self.assertIsNone(bot.S.pos);self.assertEqual(self.q,[0.,0.]);self.assertEqual(len(self.sent),2)
  self.assertFalse(bot.S.trades[-1]['real']); self.assertIsNone(bot.S.trades[-1]['u'])
 async def test_stop_loss_close_with_missing_receipt_stays_stopped(self):
  p=bot.S.pos; assert p is not None
  p.update(t=bot.now_ms()-1000,spread=0.,N=290.); self.q[:]=[-.174,.174]
  self.v.lookup_order=AsyncMock(return_value={});self.v.fills=AsyncMock(return_value={})
  await bot.close_round(bot.now_ms(),0.,'止损')
  self.assertEqual(bot.S.mode,'stopped');assert bot.S.exit_pending is not None
  self.assertTrue(bot.S.exit_pending['submitted'])
 async def test_reduce_thin_depth_still_submits_reduce_only(self):
  self.q[:]=[-.174,.174];bot.B.io['asks']=[(1700.1,.01)];bot.B.lt_b={'1660':.01};self.assertTrue(await self.v.drive_to(0,0,True,'平仓'));self.assertTrue(all(x[3] for x in self.sent));self.assertEqual(len(self.sent),2)
 async def open_signal(self):
  self.books()
  with patch.object(bot,'book_exec',return_value=(1700.,1660.,True)), patch.object(bot,'plan_notional',return_value=292.32), patch.object(bot,'free_margin',return_value=(200.,200.)), patch.object(self.v,'set_leverage',new=AsyncMock(return_value=True)):
   await bot.open_round('S',bot.now_ms(),250.,-50.)
 def prepare_entry(self):
  bot.S.pos=None;self.v.balances=AsyncMock(return_value={'io':100.,'lt':100.});self.v.fills=AsyncMock(return_value={'io':{'ok':False},'lt':{'ok':False}})
 async def test_ioc_miss_cools_down_then_next_signal_can_open(self):
  self.prepare_entry();self.first='ioc_miss'
  await self.open_signal()
  self.assertIsNone(bot.S.pos);self.assertIsNone(bot.S.exit_pending);self.assertEqual(bot.S.mode,'live');self.assertEqual(len(self.sent),1)
  self.assertGreater(bot.S.cool,bot.now_ms());self.assertEqual(bot.S.trades,[]);self.assertFalse(bot.unknown_orders());self.assertEqual(getattr(self.v.fills,'await_count'),0)
  old_id=bot.S.intents[-1]['official_id']; self.first='full';bot.S.cool=0
  await self.open_signal()
  self.assertEqual([a[0] for a in self.sent],['io','io','lt']);self.assertNotEqual(old_id,bot.S.intents[-2]['official_id']);self.assertEqual(bot.S.mode,'live');self.assertIsNotNone(bot.S.pos)
 async def test_ioc_miss_delayed_positions_never_start_second_leg_or_duplicate(self):
  self.prepare_entry();self.first='ioc_miss'
  self.v.positions=AsyncMock(side_effect=[(0.,0.,None,None),(0.,0.,None,None),(None,None,None,None),(None,None,None,None),(0.,0.,None,None)])
  await self.open_signal()
  self.assertEqual(bot.S.mode,'live');self.assertIsNotNone(bot.S.exit_pending);self.assertTrue(bot.unknown_orders());self.assertEqual(len(self.sent),1)
  await bot.reconcile_exit(bot.now_ms());self.assertEqual(bot.S.mode,'live');self.assertIsNone(bot.S.pos);self.assertIsNone(bot.S.exit_pending);self.assertEqual(len(self.sent),1);self.assertEqual(bot.S.trades,[])
 async def test_margin_reject_is_not_a_market_miss(self):
  self.prepare_entry();self.first='margin_reject';await self.open_signal()
  self.assertEqual(bot.S.mode,'stopped');self.assertEqual(len(self.sent),1);self.assertIsNone(bot.S.pos)
 async def test_delayed_margin_reject_still_stops_after_flat_is_confirmed(self):
  self.prepare_entry();self.first='margin_reject'
  self.v.positions=AsyncMock(side_effect=[(0.,0.,None,None),(0.,0.,None,None),(None,None,None,None),(None,None,None,None),(0.,0.,None,None)])
  await self.open_signal();await bot.reconcile_exit(bot.now_ms())
  self.assertEqual(bot.S.mode,'stopped');self.assertIsNone(bot.S.pos);self.assertEqual(len(self.sent),1)
 async def test_ioc_miss_never_overrides_a_stop_during_reconciliation(self):
  self.prepare_entry();self.first='ioc_miss'
  send=self.v.order_io
  async def stopped(*a):
   result=await send(*a);bot.S.mode='stopped';return result
  with patch.object(self.v,'order_io',new=stopped):await self.open_signal()
  self.assertEqual(bot.S.mode,'stopped');self.assertIsNone(bot.S.pos);self.assertEqual(len(self.sent),1);self.assertEqual(bot.S.trades,[])
 async def test_open_round_first_reject_stays_stopped_and_flat(self):
  self.first='reject';bot.S.pos=None;bot.S.recovery=False;self.v.balances=AsyncMock(return_value={'io':100.,'lt':100.});self.v.fills=AsyncMock(return_value={'io':{'ok':False},'lt':{'ok':False}})
  with patch.object(bot,'book_exec',return_value=(1700.,1660.,True)), patch.object(bot,'plan_notional',return_value=292.32), patch.object(bot,'free_margin',return_value=(200.,200.)), patch.object(self.v,'set_leverage',new=AsyncMock(return_value=True)):
   await bot.open_round('S',bot.now_ms(),250.,-50.)
  self.assertEqual(len(self.sent),1);self.assertIsNone(bot.S.pos);self.assertEqual(bot.S.mode,'stopped');self.assertFalse(bot.unknown_orders())
 async def test_open_round_partial_cleans_actual_only(self):
  self.first='partial';bot.S.pos=None;bot.S.recovery=False;self.v.balances=AsyncMock(return_value={'io':100.,'lt':100.});self.v.fills=AsyncMock(return_value={'io':{'ok':False},'lt':{'ok':False}})
  with patch.object(bot,'book_exec',return_value=(1700.,1660.,True)), patch.object(bot,'plan_notional',return_value=292.32), patch.object(bot,'free_margin',return_value=(200.,200.)), patch.object(self.v,'set_leverage',new=AsyncMock(return_value=True)):
   await bot.open_round('S',bot.now_ms(),250.,-50.)
  self.assertEqual(self.sent,[('io',.174,False,False),('io',.087,True,True)]);self.assertIsNone(bot.S.pos);self.assertEqual(bot.S.mode,'stopped');self.assertEqual(self.q,[0.,0.])
 async def test_open_round_full_pair_reaches_normal_holding(self):
  bot.S.pos=None;bot.S.recovery=False;self.v.balances=AsyncMock(return_value={'io':100.,'lt':100.});self.v.fills=AsyncMock(return_value={'io':{'ok':False},'lt':{'ok':False}})
  with patch.object(bot,'book_exec',return_value=(1700.,1660.,True)), patch.object(bot,'plan_notional',return_value=292.32), patch.object(bot,'free_margin',return_value=(200.,200.)), patch.object(self.v,'set_leverage',new=AsyncMock(return_value=True)):
   await bot.open_round('S',bot.now_ms(),250.,-50.)
  self.assertEqual(len(self.sent),2);self.assertIsNotNone(bot.S.pos);self.assertNotIn('opening_pending',bot.S.pos);self.assertEqual(bot.S.mode,'live');self.assertEqual(self.q,[-.174,.174])
 async def test_stopped_startup_does_not_initialize_signers(self):
  import ast
  tree=ast.parse(Path(bot.__file__).read_text());main=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='main');guard=next(n for n in main.body if isinstance(n,ast.If) and any(isinstance(c,ast.Call) and isinstance(c.func,ast.Attribute) and c.func.attr=='init' for c in ast.walk(n)))
  for pos,expected in [(None,False),({'live':True},True)]:
   bot.S.mode='stopped';bot.S.pos=pos;self.assertEqual(bool(eval(compile(ast.Expression(guard.test),'<guard>','eval'),bot.__dict__)),expected)
  bot.S.mode='live';bot.S.pos=None;self.assertTrue(eval(compile(ast.Expression(guard.test),'<guard>','eval'),bot.__dict__))
 async def test_dry_execution_still_completes_both_legs(self):
  bot.DRY_RUN=True
  async def dry(leg,qty,buy,px,slip,reduce):
   self.q[0 if leg=='io' else 1]+=abs(qty)*(1 if buy else -1);return {'dry':True}
  self.v.order_io=lambda *a:dry('io',*a);self.v.order_lt=lambda *a:dry('lt',*a)
  self.assertTrue(await self.drive());self.assertEqual(self.q,[-.174,.174]);self.assertFalse(bot.unknown_orders())
if __name__=='__main__':unittest.main(verbosity=2)
