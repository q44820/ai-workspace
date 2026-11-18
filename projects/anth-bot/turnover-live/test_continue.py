"""Offline continuation guards. No production access."""
import json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import core
from test_live import ControllerFixture
class Continuation(unittest.IsolatedAsyncioTestCase):
 def state(self,p):
  c=core.Controller(p,lambda:None);l=core.Ledger()
  for side in [1,-1]:l.add(dict(venue='io',id=str(side),side=side,qty=.01,price=1700,fee=.001,pnl=0))
  c.state.update(session='existing-session',started=1700000000,phase='stopped',resumeReady=True,recovery={'verified':True},config=dict(target=1000,round_cap=50,loss=3),journal=[dict(outcome='terminal',venue='io',purpose='open',qty=.01,side=1,created=1700000000)],position=None);c.ledger=l;c.persist();return c
 async def test_manual_continue_keeps_session_ledger_budget_and_funding_origin(self):
  with tempfile.TemporaryDirectory() as t,patch('core.other_service_active',return_value=False):
   p=Path(t)/'s';self.state(p);v=ControllerFixture();c=core.Controller(p,lambda:v);oldfills=c.state['fills'][:]
   self.assertTrue(c.public()['canResume']);self.assertFalse(c.running)
   await c.start({},True);c.stop.set();await c.task
   self.assertEqual(c.state['session'],'existing-session');self.assertEqual(c.state['started'],1700000000);self.assertEqual(c.state['fills'],oldfills);self.assertAlmostEqual(c.ledger.total,34)
   self.assertFalse(c.state['resumeReady']);self.assertEqual(len(c.state['journal']),1)
 async def test_manual_continue_cannot_expand_prior_limits(self):
  with tempfile.TemporaryDirectory() as t,patch('core.other_service_active',return_value=False):
   p=Path(t)/'s';c=self.state(p);c.config=core.Config(target=500,round_cap=20,loss=1);c.persist();c=core.Controller(p,lambda:ControllerFixture())
   with self.assertRaises(ValueError):await c.start(dict(target=1000,round_cap=50,loss=3),True)
 async def test_resume_flag_does_not_override_unknown_or_nonflat_ledger(self):
  with tempfile.TemporaryDirectory() as t,patch('core.other_service_active',return_value=False):
   c=self.state(Path(t)/'s');c.state['journal'][0]['outcome']='unknown'
   self.assertFalse(c.public()['canResume'])
   with self.assertRaises(core.Blocked):await c.start({},True)
 async def test_continue_rechecks_real_positions_before_running(self):
  with tempfile.TemporaryDirectory() as t,patch('core.other_service_active',return_value=False):
   p=Path(t)/'s';self.state(p);v=ControllerFixture();v.exposure=lambda:self.nonflat();c=core.Controller(p,lambda:v)
   with self.assertRaises(core.Blocked):await c.start({},True)
   self.assertFalse(c.running);self.assertEqual(c.state['session'],'existing-session')
 async def nonflat(self):return dict(io=.01,lt=0)
if __name__=='__main__':unittest.main()
