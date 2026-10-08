import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from types import SimpleNamespace

from linux.service import Recorder, make_server


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfgs = self.root/'configs'
        self.cfgs.mkdir()
        (self.cfgs/'day').mkdir()
        self.cfg = self.cfgs/'day/new.cfg'
        self.cfg.write_text('0(10)0/0;1(11)17.5/0;', 'utf-8')
        self.config_path = self.root/'hardware.json'
        self.config = dict(routing_path=str(self.cfg), prepared_fixed_routing=True,
                           sample_rate_verified=True, exclusive_saving_confirmed=True,
                           sample_rate=20000, well=0, filter='iir', reader_path=str(self.root/'reader'))
        self.config_path.write_text(json.dumps(self.config), 'utf-8')
        self.rec = Recorder(self.root/'recordings', 'maxlab', self.config,
                            routing_root=self.cfgs, config_path=self.config_path, auto_analyze=False)
        self.entry = self.rec.list_routing()['files'][0]

    def tearDown(self):
        self.tmp.cleanup()

    def select(self, downloaded=False):
        return self.rec.select_routing(self.entry['file'], self.entry['sha256'], downloaded)

    def test_selection_exports_mapping_and_requires_explicit_download_confirmation(self):
        with patch('linux.acquisition.importlib.import_module', side_effect=AssertionError('must not access SDK')):
            result = self.select()
            self.assertFalse(result['routing']['prepared'])
            mapping = json.loads(Path(self.rec.config['mapping_path']).read_text('utf-8'))
            self.assertEqual([r['channel'] for r in mapping], [0, 1])
            self.assertEqual(self.rec.config['sample_rate'], 20000)
            self.assertEqual(self.rec.config['routing_sha256'], hashlib.sha256(self.cfg.read_bytes()).hexdigest())
            with self.assertRaises(ValueError):
                self.rec.start('not-downloaded', 600)
            self.assertIsNone(self.rec.job)
            result = self.select(True)
            self.assertTrue(result['routing']['prepared'])
            saved = json.loads(self.config_path.read_text('utf-8'))
            self.assertTrue(saved['prepared_fixed_routing'])
            restored = Recorder(self.root/'restored', 'maxlab', saved)
            self.assertEqual(restored.routing_files.root, self.cfgs.resolve())
            self.assertEqual(restored.routing_status()['path'], str(self.cfg.resolve()))

    def test_traversal_changed_file_bad_confirmation_and_active_tasks_rejected(self):
        outside = self.root/'outside.cfg'
        outside.write_text('0(10)0/0;', 'utf-8')
        for name in ('../outside.cfg', str(outside.resolve())):
            with self.assertRaises(ValueError):
                self.rec.select_routing(name, self.entry['sha256'], True)
        with self.assertRaises(ValueError):
            self.select('true')
        for state in ('recording', 'finalizing'):
            self.rec.job = dict(state=state)
            with self.assertRaises(ValueError):
                self.select(True)
        self.rec.job = None
        self.rec.analysis = SimpleNamespace(snapshot=lambda: dict(state='running'))
        with self.assertRaises(ValueError):
            self.select(True)
        self.rec.analysis = None
        self.rec.hardware_fault = True
        with self.assertRaises(ValueError):
            self.select(True)
        self.rec.hardware_fault = False
        self.cfg.write_text('0(12)0/0;', 'utf-8')
        with self.assertRaisesRegex(ValueError, '已改变'):
            self.select(True)

    def test_changed_confirmed_cfg_cannot_start_capture(self):
        self.select(True)
        self.cfg.write_text('0(12)0/0;', 'utf-8')
        with patch('linux.service.MaxlabSource', side_effect=AssertionError('must not access SDK')):
            with self.assertRaisesRegex(ValueError, 'CFG 文件改变'):
                self.rec.start('changed-cfg-test', 600)

    def test_invalid_files_are_not_offered_and_mock_does_not_support_selection(self):
        (self.cfgs/'invalid.cfg').write_text('invalid', 'utf-8')
        self.assertEqual(len(self.rec.list_routing()['files']), 1)
        mock = Recorder(self.root/'mock', auto_analyze=False)
        self.assertNotIn('linux_routing_v1', mock.snapshot()['capabilities'])
        with self.assertRaises(ValueError):
            mock.select_routing(self.entry['file'], self.entry['sha256'], True)

    def test_http_routing_uses_auth_and_returns_snapshot_after_selection(self):
        token = 'routing-test-token'
        server = make_server(self.rec, '127.0.0.1', 0, token)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = f'http://127.0.0.1:{server.server_port}/v1/routing'
        def request(data=None, auth=token):
            req = urllib.request.Request(base, data=json.dumps(data).encode() if data is not None else None,
                                          headers={'Authorization': 'Bearer '+auth})
            with opener.open(req, timeout=3) as response:
                return json.load(response)
        try:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                request(auth='wrong')
            self.assertEqual(caught.exception.code, 401)
            catalog = request()
            self.assertEqual(catalog['files'][0]['channels'], 2)
            result = request(dict(file=self.entry['file'], sha256=self.entry['sha256'], downloaded=False))
            self.assertFalse(result['routing']['prepared'])
            self.assertIn('linux_routing_v1', result['capabilities'])
            self.assertIsNone(result['job'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
