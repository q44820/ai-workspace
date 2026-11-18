"""Offline fixtures for eventual receipts and asynchronous UI start. Never real orders."""
import asyncio,importlib.util,tempfile,time,types,unittest
from pathlib import Path
from unittest.mock import AsyncMock,Mock,patch
import adapter,core
class ReceiptFlow(unittest.IsolatedAsyncioTestCase):
 async def test_empty_aggregate_is_pending_not_permanent_failure(self):
  v=adapter.Venue();v.index=7;v.get=AsyncMock(return_value=dict(code=200,trades=[],next_cursor=''))
  with self.assertRaises(core.ReceiptPending):await v.aggregate_fills(dict(side=1),12,[])
 async def test_terminal_receipt_lag_retries_reads_never_submit(self):
  v=adapter.Venue();v.order=AsyncMock(return_value=dict(terminal=True,filled=.014,id=12));f=dict(venue='lt',id='r',side=1,qty=.014,price=1700,fee=0,pnl=0)
  v.fills=AsyncMock(side_effect=[core.ReceiptPending('fixture lag'),TimeoutError('fixture transport'),[f]]);v.submit=AsyncMock()
  with patch('adapter.asyncio.sleep',new=AsyncMock()):r=await v.settle(dict(qty=.014,created=time.time()),dict(maker=False),asyncio.Event())
  self.assertEqual(r['fills'],[f]);self.assertEqual(v.fills.await_count,3);v.submit.assert_not_called()
 async def test_wrong_identity_is_not_retried_as_pending(self):
  v=adapter.Venue();v.order=AsyncMock(return_value=dict(terminal=True,filled=.014,id=12));v.fills=AsyncMock(side_effect=core.Blocked('fixture identity mismatch'))
  with self.assertRaises(core.Blocked):await v.settle(dict(qty=.014,created=time.time()),dict(maker=False),asyncio.Event())
  self.assertEqual(v.fills.await_count,1)
 async def test_receipt_lag_deadline_freezes_without_reorder(self):
  v=adapter.Venue();v.order=AsyncMock(return_value=dict(terminal=True,filled=.014,id=12));v.fills=AsyncMock(side_effect=core.ReceiptPending('fixture lag'));clock=[0]
  async def tick(_):clock[0]+=20
  with patch('adapter.time.monotonic',side_effect=lambda:clock[0]),patch('adapter.asyncio.sleep',side_effect=tick):
   with self.assertRaises(core.Blocked):await v.settle(dict(qty=.014,created=time.time()),dict(maker=False),asyncio.Event())
  self.assertLessEqual(v.fills.await_count,5)
 async def test_slow_start_returns_accepted_and_get_remains_available(self):
  from aiohttp.test_utils import TestClient,TestServer
  spec=importlib.util.spec_from_file_location('api_flow',Path(__file__).with_name('server.py'));assert spec is not None and spec.loader is not None;api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)
  from test_live import ControllerFixture
  v=ControllerFixture();gate=asyncio.Event()
  async def preflight():await gate.wait()
  v.preflight=preflight
  with tempfile.TemporaryDirectory() as t,patch('core.other_service_active',return_value=False):
   client=TestClient(TestServer(api.create_app(Path(t)/'state','x'*40,lambda:v)));await client.start_server()
   try:
    h={'Authorization':'Bearer '+'x'*40};d=await (await client.get('/state',headers=h)).json();h['X-Turnover-CSRF']=d['csrf'];body=dict(confirm='用户手动启动本次真实合约小额试验',config={})
    r=await asyncio.wait_for(client.post('/start',headers=h,json=body),1);self.assertEqual(r.status,202);d=await r.json();self.assertTrue(d['startPending'])
    d=await (await client.get('/state',headers=h)).json();self.assertTrue(d['startPending']);self.assertFalse(d['running'])
    r=await client.post('/start',headers=h,json=body);self.assertEqual(r.status,409)
    await client.post('/stop',headers=h,json={});gate.set()
    for _ in range(50):
     d=await (await client.get('/state',headers=h)).json()
     if not d['startPending']:break
     await asyncio.sleep(.01)
    self.assertFalse(d['running']);self.assertEqual(v.sent,[])
   finally:gate.set();await client.close()
if __name__=='__main__':unittest.main()
