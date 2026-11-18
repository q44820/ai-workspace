"""Offline fixtures and mock HTTP only. No wallet, signing, control API or real writes."""
import io
import json
import time
import unittest
from unittest.mock import patch
import publish_status as pub


class PublisherTests(unittest.TestCase):
    def snapshot(self):
        return dict(ok=True, ts=time.time()*1000, mode='live', liveEnabled=True,
                    dryRun=False, venuesReady=True, stale=False, alert=pub.LEGACY_NOTICE)

    def test_legacy_notice_only_reclassified_in_safe_live_mode(self):
        s=self.snapshot(); out=pub.presentation(s)
        self.assertIsNone(out['alert']);self.assertIn('历史',out['statusNotice'])
        self.assertEqual(s['alert'],pub.LEGACY_NOTICE)
        for key,value in [('mode','stopped'),('recoveryExposure',True),('exit_pending',{'unknown':True}),('stale',True),('liveEnabled',False),('dryRun',True),('venuesReady',False),('alert','unknown order')]:
            changed={**s,key:value};self.assertEqual(pub.presentation(changed)['alert'],changed['alert'])

    def test_only_get_local_public_and_post_existing_ingest(self):
        calls=[];s=self.snapshot()
        class Opener:
            def open(self, request, timeout):
                calls.append(request)
                return io.BytesIO(json.dumps(s if len(calls)==1 else {'ok':True}).encode())
        cfg=dict(PUBLIC_PATH='fixture-only',PUBLIC_PORT='8788',STATUS_PUSH_URL='https://anth-live.vercel.app/api/ingest',STATUS_PUSH_TOKEN='offline-dummy')
        self.assertTrue(pub.publish_once(cfg,Opener()))
        self.assertEqual(calls[0],'http://127.0.0.1:8788/fixture-only')
        self.assertEqual(calls[1].get_method(),'POST');self.assertEqual(calls[1].full_url,cfg['STATUS_PUSH_URL'])
        body=json.loads(calls[1].data);self.assertEqual(body['ts'],s['ts']);self.assertIsNone(body['alert'])

    def test_stale_or_invalid_source_is_not_retimestamped_or_sent(self):
        for stamp in [None,True,float('nan'),0,time.time()*1000+50000]:
            calls=[];s={**self.snapshot(),'ts':stamp}
            class Opener:
                def open(self, request, timeout):
                    calls.append(request);return io.BytesIO(json.dumps(s).encode())
            cfg=dict(PUBLIC_PATH='fixture-only',PUBLIC_PORT='8788')
            with self.assertRaises(AssertionError):pub.publish_once(cfg,Opener())
            self.assertEqual(len(calls),1)


if __name__=='__main__':unittest.main()
