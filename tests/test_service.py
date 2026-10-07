import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

import h5py
import numpy as np
from linux.acquisition import MaxlabSource, validate_mapping
from linux.service import Recorder, make_server


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rec = Recorder(self.tmp.name, auto_analyze=False)

    def tearDown(self):
        if self.rec.thread and self.rec.thread.is_alive():
            self.rec.stop(self.rec.job['job_id'])
            self.rec.thread.join(8)
        self.tmp.cleanup()

    def finish(self):
        self.rec.thread.join(8)
        self.assertFalse(self.rec.thread.is_alive())
        return self.rec.snapshot()['job']

    def test_complete_h5_counts_rates_and_silent_channels(self):
        self.rec.start('complete-test', .3)
        job = self.finish()
        self.assertEqual(job['state'], 'completed', job['error'])
        self.assertEqual(job['elapsed_s'], .3)
        with h5py.File(job['files'][0]) as f:
            self.assertTrue(f.attrs['complete'])
            self.assertFalse(f.attrs['contains_raw_voltage'])
            self.assertEqual(f.attrs['mode'], 'mock')
            spikes = f['data_store/data0000/spikes'][:]
            self.assertEqual(len(spikes), job['total_spikes'])
            counts = np.bincount(spikes['channel'], minlength=1024)
            for row in job['rates']:
                self.assertAlmostEqual(row['hz'], counts[row['channel']]/job['window_s'])
            self.assertEqual(job['rates'][0]['hz'], 0)
            self.assertEqual(len(job['rates']), 1024)

    def test_idempotent_start_and_restart(self):
        first = self.rec.start('dedup-test', .2)
        second = self.rec.start('dedup-test', .2)
        self.assertEqual(first['job_id'], second['job_id'])
        with self.assertRaises(ValueError):
            self.rec.start('different-request', .2)
        with self.assertRaises(ValueError):
            self.rec.start('dedup-test', .4)
        before = self.finish()
        restored = Recorder(self.tmp.name)
        self.assertEqual(restored.start('dedup-test', .2)['job_id'], first['job_id'])
        self.assertEqual(restored.snapshot()['job']['rates'], before['rates'])

    def test_stop_is_not_completion(self):
        job = self.rec.start('stop-test', 600)
        time.sleep(.12)
        with self.assertRaises(ValueError):
            self.rec.stop('incorrect-job')
        self.rec.stop(job['job_id'])
        result = self.finish()
        self.assertEqual(result['state'], 'stopped')
        self.assertLess(result['elapsed_s'], 600)
        with h5py.File(result['files'][0]) as f:
            self.assertFalse(f.attrs['complete'])

    def test_bad_input_and_disk_space(self):
        for value in [0, -1, 601, float('nan'), True, '600']:
            with self.assertRaises(ValueError):
                self.rec.start('input-test', value)
        with patch('linux.service.shutil.disk_usage') as disk:
            disk.return_value.free = 1
            with self.assertRaises(ValueError):
                self.rec.start('disk-test', .1)

    def test_source_failure_preserves_incomplete_h5(self):
        with patch('linux.service.MockSource.read', side_effect=RuntimeError('stream lost')):
            self.rec.start('failure-test', .3)
            result = self.finish()
        self.assertEqual(result['state'], 'failed')
        self.assertIn('stream lost', result['error'])
        self.assertTrue(Path(result['files'][0]).exists())
        with h5py.File(result['files'][0]) as f:
            self.assertFalse(f.attrs['complete'])

    def test_cleanup_failure_never_reports_completed(self):
        with patch('linux.service.MockSource.close', side_effect=RuntimeError('save close failed')):
            self.rec.start('cleanup-test', .1)
            result = self.finish()
        self.assertEqual(result['state'], 'failed')
        self.assertIn('save close failed', result['error'])

    def test_restart_marks_active_task_interrupted(self):
        self.rec.start('restart-test', .1)
        result = self.finish()
        path = Path(result['directory'])/'session.json'
        saved = json.loads(path.read_text('utf-8'))
        saved.update(state='recording', mode='maxlab')
        path.write_text(json.dumps(saved), encoding='utf-8')
        restored = Recorder(self.tmp.name, 'maxlab')
        self.assertEqual(restored.job['state'], 'interrupted')
        self.assertTrue(restored.hardware_fault)
        with self.assertRaises(ValueError):
            restored.start('new-hardware-task', 600)

    def test_600_second_clock_without_waiting_600_seconds(self):
        # Exercise real loop/end condition at 600 seconds with a deterministic clock.
        class Source:
            rate = 20
            mapping = [dict(channel=0, electrode=10, x=0., y=0.)]
            def start(self, directory):
                self.started = clock[0]
            def read(self, stop):
                first = round((clock[0]-self.started)*20)
                clock[0] += 1
                return dict(first=first, last=first+19, spikes=[])
            def close(self):
                return []
        clock = [100.]
        with patch('linux.service.MockSource', Source), patch('linux.service.time.monotonic', lambda: clock[0]):
            self.rec.start('full-duration-test', 600)
            result = self.finish()
        self.assertEqual(result['state'], 'completed', result['error'])
        self.assertEqual(result['acquired_s'], 600)
        self.assertEqual(result['elapsed_s'], 600)
        self.assertEqual(result['total_spikes'], 0)

    def test_raster_decimation_does_not_reduce_rates(self):
        self.rec.start('decimation-test', .1)
        self.finish()
        with self.rec.lock:
            self.rec.events.clear()
            self.rec.events.extend((0., 0, -40.) for _ in range(10000))
            self.rec.job['window_s'] = 5
        result = self.rec.snapshot()['job']
        self.assertLessEqual(len(result['raster']), 8000)
        self.assertEqual(result['rates'][0]['hz'], 2000)
        self.assertTrue(result['raster_sampled'])

    def test_hardware_gates_and_mapping(self):
        with self.assertRaises(ValueError):
            MaxlabSource({})
        with self.assertRaises(ValueError):
            validate_mapping([dict(channel=0, electrode=1, x=0, y=0)]*2)


class HttpTests(unittest.TestCase):
    def test_reconnect_recovers_same_job_and_auth(self):
        with tempfile.TemporaryDirectory() as root:
            rec = Recorder(root, auto_analyze=False)
            server = make_server(rec, '127.0.0.1', 0, 'test-secret-token')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def request(path, data=None, token='test-secret-token'):
                req = urllib.request.Request(base+path, data=json.dumps(data).encode() if data else None,
                    headers={'Authorization': 'Bearer '+token})
                with opener.open(req, timeout=3) as response:
                    return json.load(response)
            try:
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    request('/v1/status', token='bad')
                self.assertEqual(caught.exception.code, 401)
                first = request('/v1/start', dict(request_id='http-request', duration_s=.3))
                time.sleep(.1)  # no client connected; recording must continue
                status = request('/v1/status')['job']
                self.assertEqual(first['job_id'], status['job_id'])
                self.assertGreater(status['elapsed_s'], 0)
                rec.thread.join(5)
                self.assertEqual(request('/v1/status')['job']['state'], 'completed')
            finally:
                if rec.thread:
                    rec.thread.join(5)
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main(verbosity=2)
