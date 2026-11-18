"""OFFLINE API -> warmup -> real controller/adapter lifecycle -> target -> flat.
All order/market fixtures are explicitly synthetic and are never live evidence.
"""
import asyncio,tempfile,time,types,unittest,importlib.util
from pathlib import Path
from unittest.mock import patch
import adapter,core
class FullLifecycle(unittest.IsolatedAsyncioTestCase):
 async def test_api_multiround_receipt_lag_target_and_flat_restart(self):
  from aiohttp.test_utils import TestClient,TestServer
  spec=importlib.util.spec_from_file_location('full_api',Path(__file__).with_name('server.py'));assert spec is not None and spec.loader is not None;api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)
  real_sleep=asyncio.sleep;clock=[time.time()];records=[]
  class OfflineVenue(adapter.Venue):
   io_dec=3;lt_dec=3
   def __init__(self):super().__init__();self.balance={'io':0.,'lt':0.};self.orders={};self.reads={};self.counter=0
   async def initialize(self):pass
   async def close(self):pass
   async def no_orders(self):pass
   async def preflight(self):pass
   async def book(self):return {'at':clock[0],**{k:{'bids':[[1700.,10]],'asks':[[1700.1,10]]} for k in ['io','lt']}}
   async def funding(self,since):return 0.
   async def exposure(self):return dict(self.balance)
   async def submit(self,i,maker=False):
    self.counter+=1;assert i['client'] not in self.orders
    if i['reduce']:assert self.balance[i['venue']]*i['side']<0 and i['qty']<=abs(self.balance[i['venue']])+1e-9
    self.balance[i['venue']]+=i['side']*i['qty'];self.orders[i['client']]=dict(i);records.append(dict(i));return dict(id=self.counter,maker=maker,rejected=False)
   async def order(self,i):return dict(terminal=True,filled=i['qty'],id=i['client'])
   async def fills(self,i,oid):
    self.reads[oid]=self.reads.get(oid,0)+1
    if i['venue']=='lt' and self.reads[oid]<3:raise core.ReceiptPending('OFFLINE delayed receipt fixture')
    return [dict(venue=i['venue'],id=str(oid),side=i['side'],qty=i['qty'],price=i['price'],fee=.0005,pnl=0.)]
  async def advance(_):clock[0]+=10;await real_sleep(.001)
  v=OfflineVenue()
  with tempfile.TemporaryDirectory() as t,patch('core.other_service_active',return_value=False),patch('core.time',types.SimpleNamespace(time=lambda:clock[0])),patch('core.asyncio.sleep',side_effect=advance):
   state=Path(t)/'state.json';client=TestClient(TestServer(api.create_app(state,'x'*40,lambda:v)));await client.start_server();h={'Authorization':'Bearer '+'x'*40}
   try:
    d=await (await client.get('/state',headers=h)).json();h['X-Turnover-CSRF']=d['csrf']
    r=await client.post('/start',headers=h,json=dict(confirm='用户手动启动本次真实合约小额试验',config=dict(target=100,round_cap=50,loss=3)));self.assertEqual(r.status,202)
    seen=[]
    for _ in range(1500):
     d=await (await client.get('/state',headers=h)).json();seen.append(d['phase'])
     if not d['running'] and not d['startPending'] and d['phase'] in {'finished','needs_review','rejected'}:break
     await real_sleep(.002)
    self.assertEqual(d['phase'],'finished',str(d));self.assertGreaterEqual(d['volume']['both'],100);self.assertEqual(d['orders'][-1]['outcome'],'terminal');self.assertGreaterEqual(len(records),8);self.assertTrue(any(x['reduce'] for x in records));self.assertEqual(await v.exposure(),{'io':0.,'lt':0.});self.assertTrue(all(vv>=3 for k,vv in v.reads.items() if v.orders[k]['venue']=='lt'));self.assertIsNone(d['position']);self.assertTrue(all(x['outcome']=='terminal' for x in d['orders']));self.assertEqual(len(records),len(set(x['client'] for x in records)))
    reopened=core.Controller(state,lambda:None);self.assertFalse(reopened.running);self.assertEqual(reopened.ledger.total,d['volume']['both']);self.assertEqual(reopened.state['session'],d['session'])
   finally:await client.close()
if __name__=='__main__':unittest.main()
