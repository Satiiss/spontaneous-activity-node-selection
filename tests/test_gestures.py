import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
from linux.service import Recorder, make_server
from shared.gestures import GESTURES, GESTURE_PATH

class GestureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rec = Recorder(self.tmp.name, auto_analyze=False)
    def tearDown(self):
        self.tmp.cleanup()
    def event(self, gid=3, eid='test-event-0001'):
        return dict(event_id=eid, gesture_id=gid, frame_index=42, hand='left')
    def test_five_gestures_durable_idempotent_without_hardware(self):
        with patch('linux.acquisition.importlib.import_module', side_effect=AssertionError('no SDK calls')):
            for gid, name in GESTURES.items():
                data=self.event(gid, 'gesture-'+str(gid))
                receipt=self.rec.gesture_log.receive(data)
                self.assertEqual(receipt['gesture'],name)
                self.assertTrue(receipt['received'])
                self.assertFalse(receipt['stimulated'])
            restored=Recorder(self.tmp.name, auto_analyze=False)
            replay=restored.gesture_log.receive(self.event(3,'gesture-3'))
            self.assertTrue(replay['duplicate'])
            with self.assertRaises(ValueError):
                restored.gesture_log.receive(self.event(14,'gesture-3'))
            self.assertIsNone(restored.job)
    def test_validation(self):
        for field, value in [('event_id','../bad'),('gesture_id',True),('gesture_id',99),('hand','both'),('frame_index',-1),('frame_index',True)]:
            data=self.event();data[field]=value
            with self.assertRaises(ValueError): self.rec.gesture_log.receive(data)
    def test_http_authenticated_five_gestures(self):
        server=make_server(self.rec,'127.0.0.1',0,'test-token-0123456789')
        thread=threading.Thread(target=server.serve_forever);thread.start()
        def post(data, token='test-token-0123456789'):
            req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+GESTURE_PATH,
                data=json.dumps(data).encode(),headers={'Authorization':'Bearer '+token})
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=3) as response:
                return json.load(response)
        try:
            with self.assertRaises(urllib.error.HTTPError) as error: post(self.event(), 'wrong')
            self.assertEqual(error.exception.code,401)
            for gid in GESTURES:
                self.assertEqual(post(self.event(gid,'http-gesture-'+str(gid)))['gesture_id'],gid)
            with self.assertRaises(urllib.error.HTTPError) as error: post(self.event(99))
            self.assertEqual(error.exception.code,400)
            self.assertIn('gesture_events_v1',self.rec.snapshot()['capabilities'])
        finally:
            server.shutdown();thread.join();server.server_close()

if __name__=='__main__': unittest.main()
