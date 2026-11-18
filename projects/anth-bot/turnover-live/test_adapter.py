import unittest
from unittest.mock import AsyncMock
import adapter
from core import Blocked

class Adapter(unittest.IsolatedAsyncioTestCase):
 async def test_public_status_does_not_initialize_signers(self):
  v=adapter.Venue();self.assertIsNone(v.hl);self.assertIsNone(v.lt)
 async def test_io_accounting_requires_real_fee_token(self):
  v=adapter.Venue();v.address='fixture';v.post=AsyncMock(return_value=[dict(coin='io:ANTH',oid=12,side='B',feeToken='USDC',tid=13,sz='.01',px='1700',fee='.0017',closedPnl='0')]);i=dict(venue='io',side=1,created=1)
  f=await v.fills(i,12);self.assertEqual(f[0]['fee'],.0017);self.assertEqual(f[0]['qty']*f[0]['price'],17)
  v.post.return_value[0]['feeToken']='UNKNOWN'
  with self.assertRaises(Blocked):await v.fills(i,12)
 async def test_lt_account_specific_fills_and_ppm_fee(self):
  class D:
   def to_dict(self):return {'code':200,'trades':[dict(bid_id=12,market_id=193,bid_account_id=7,bid_client_id=9,is_maker_ask=True,taker_fee=280,integrator_taker_fee=0,size='.01',price='1700',trade_id=88,bid_account_pnl='0')]}
  class O:trades=AsyncMock(return_value=D())
  class L:order_api=O()
  v=adapter.Venue();v.lt=L();v.index=7;v.auth=lambda:'fixture';i=dict(venue='lt',side=1,client=9)
  f=await v.fills(i,12);self.assertAlmostEqual(f[0]['fee'],17*.00028)
 async def test_crossed_or_stale_book_blocks(self):
  v=adapter.Venue();v.post=AsyncMock(return_value={'coin':'io:ANTH','time':0,'levels':[]});v.get=AsyncMock(return_value={})
  with self.assertRaises(Blocked):await v.book()
 async def test_unverified_funding_is_not_zero(self):
  v=adapter.Venue();v.post=AsyncMock(side_effect=OSError('fixture'))
  class A:position_funding=AsyncMock(side_effect=OSError('fixture'))
  v.accounts=A();v.auth=lambda:'fixture';v.index=7;v.address='fixture'
  with self.assertRaises(OSError):await v.funding(1)

 async def test_funding_response_seconds_vs_query_milliseconds(self):
  class D:
   def to_dict(self):return {'code':200,'position_fundings':[{'timestamp':1700000001,'market_id':193,'change':'-.02'},{'timestamp':1699999999,'market_id':193,'change':'9'}]}
  class A:position_funding=AsyncMock(return_value=D())
  v=adapter.Venue();v.accounts=A();v.index=7;v.address='fixture';v.auth=lambda:'fixture';v.post=AsyncMock(return_value=[{'delta':{'coin':'io:ANTH','usdc':'.01'}}])
  self.assertAlmostEqual(await v.funding(1700000000),-.01)

 async def test_funding_uses_installed_sdk_signature(self):
  class D:
   def to_dict(self):return {'code':200,'position_fundings':[],'next_cursor':''}
  class A:
   async def position_funding(self,account_index,limit,authorization=None,cursor=None,side=None,start_timestamp=None,end_timestamp=None,market_ids=None,_request_timeout=None):
    assert market_ids==[193] and start_timestamp==1700000000000
    return D()
  v=adapter.Venue();v.accounts=A();v.index=7;v.address='fixture';v.auth=lambda:'fixture';v.post=AsyncMock(return_value=[])
  self.assertEqual(await v.funding(1700000000),0)

 async def test_funding_transient_error_is_not_silently_converted_to_none(self):
  class A:position_funding=AsyncMock(side_effect=TimeoutError('fixture'))
  v=adapter.Venue();v.accounts=A();v.index=7;v.address='fixture';v.auth=lambda:'fixture';v.post=AsyncMock(return_value=[])
  with self.assertRaises(TimeoutError):await v.funding(1700000000)

if __name__=='__main__':unittest.main()
