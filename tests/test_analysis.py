import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

import numpy as np
import h5py

from linux.analysis import AnalysisTask
from linux.analysis_worker import analyze_file, load_config
from linux.service import Recorder, make_server
from tests.analysis_fixture import write_recording

CONFIG = Path(__file__).resolve().parents[1]/'linux'/'analysis.yaml'


class AnalysisTests(unittest.TestCase):
    def test_original_rules_with_real_file_and_process_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            job = write_recording(directory)
            task = AnalysisTask(job, CONFIG)
            first = task.start('analysis-test-request')
            duplicate = task.start('another-analysis-request')
            self.assertEqual(first['analysis_id'], duplicate['analysis_id'])
            task.thread.join(30)
            self.assertFalse(task.thread.is_alive())
            state = task.snapshot()
            self.assertEqual(state['state'], 'completed', state['error'])
            self.assertEqual(state['progress'], 100)
            result = state['result']
            self.assertEqual(result['spike_count'], job['total_spikes'])
            self.assertGreater(result['burst_count'], 30)
            self.assertGreater(result['candidate_count'], 0)
            self.assertEqual(result['candidate_count'], len(result['candidates']))
            self.assertTrue(all(r['high_outdegree'] for r in result['candidates']))
            self.assertEqual(result['config']['high_outdegree']['percentile'], 90)
            stages = {entry['stage'] for entry in state['log']}
            self.assertTrue({'reading', 'bursts', 'activation', 'connections', 'selection', 'saving', 'completed'} <= stages)
            self.assertTrue((Path(state['directory'])/'result.json').exists())
            restored = AnalysisTask(job, CONFIG)
            self.assertEqual(restored.snapshot()['result'], result)
            self.assertEqual(restored.start('new-request-on-success')['analysis_id'], first['analysis_id'])

    def test_silent_recording_completes_without_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            job = write_recording(directory, silent=True)
            with contextlib.redirect_stdout(io.StringIO()):
                result = analyze_file(job['files'][0], Path(directory)/'output', load_config(CONFIG), 'mock')
            self.assertEqual(result['candidate_count'], 0)
            self.assertEqual(result['burst_count'], 0)
            self.assertIsNone(result['threshold'])
            json.dumps(result, allow_nan=False)

    def test_native_wells_layout_matches_legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = write_recording(Path(directory)/'legacy')
            native = write_recording(Path(directory)/'native', layout='wells')
            with contextlib.redirect_stdout(io.StringIO()):
                a = analyze_file(legacy['files'][0], Path(directory)/'a', load_config(CONFIG), 'mock')
                b = analyze_file(native['files'][0], Path(directory)/'b', load_config(CONFIG), 'maxlab')
            self.assertEqual(a['candidates'], b['candidates'])
            self.assertEqual(a['edge_count'], b['edge_count'])

    def test_incomplete_recording_and_mapping_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            job = write_recording(directory)
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                analyze_file(job['files'][0], Path(directory)/'out', load_config(CONFIG), 'mock', expected_mapping=[])
            with h5py.File(job['files'][0], 'r+') as f:
                f.attrs['complete'] = False
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                analyze_file(job['files'][0], Path(directory)/'out', load_config(CONFIG), 'mock')
            self.assertFalse((Path(directory)/'out'/'result.json').exists())

    def test_native_unmapped_events_excluded_only_with_confirmed_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            job = write_recording(directory, layout='wells', silent=True)
            path = Path(job['files'][0])
            with h5py.File(path, 'r+') as f:
                g = f['wells/well000/rec0000']
                dtype = g['spikes'].dtype
                del g['spikes']
                g.create_dataset('spikes', data=np.array([(12345, 355, -9.)], dtype=dtype))
            with contextlib.redirect_stdout(io.StringIO()):
                result = analyze_file(path, Path(directory)/'verified', load_config(CONFIG), 'maxlab',
                                      expected_mapping=job['mapping'], expected_rate=20000)
            self.assertEqual(result['excluded_unmapped_spikes'], 1)
            self.assertEqual(result['spike_count'], 0)
            self.assertEqual(result['candidate_count'], 0)
            with h5py.File(path) as f:
                self.assertEqual(len(f['wells/well000/rec0000/spikes']), 1)
            for mode, expected in [('file', None), ('maxlab', None), ('maxlab', [])]:
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                    analyze_file(path, Path(directory)/'rejected', load_config(CONFIG), mode,
                                 expected_mapping=expected)

    def test_failed_analysis_preserves_recording_and_can_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            job = write_recording(directory)
            with h5py.File(job['files'][0], 'r+') as f:
                f.attrs['complete'] = False
            rec = Recorder(directory, auto_analyze=False)
            first = rec.analyze(job['job_id'], 'failed-analysis-request')
            rec.wait_analysis(30)
            self.assertEqual(rec.snapshot()['analysis']['state'], 'failed')
            self.assertEqual(rec.snapshot()['job']['state'], 'completed')
            with h5py.File(job['files'][0], 'r+') as f:
                f.attrs['complete'] = True
            second = rec.analyze(job['job_id'], 'retry-analysis-request')
            self.assertNotEqual(first['analysis_id'], second['analysis_id'])
            rec.wait_analysis(30)
            self.assertEqual(rec.snapshot()['analysis']['state'], 'completed')

    def test_cancel_and_restart_do_not_claim_success(self):
        with tempfile.TemporaryDirectory() as directory:
            job = write_recording(directory)
            rec = Recorder(directory, auto_analyze=False)
            start = rec.analyze(job['job_id'], 'cancel-analysis-request')
            with self.assertRaises(ValueError):
                rec.cancel_analysis(job['job_id'], 'wrong-analysis')
            rec.cancel_analysis(job['job_id'], start['analysis_id'])
            rec.wait_analysis(30)
            self.assertEqual(rec.snapshot()['analysis']['state'], 'cancelled')
            self.assertIsNone(rec.snapshot()['analysis']['result'])
            status = rec.snapshot()['analysis']
            status['state'] = 'running'
            (Path(job['directory'])/'analysis_state.json').write_text(json.dumps(status), 'utf-8')
            restored = Recorder(directory, auto_analyze=False)
            self.assertEqual(restored.snapshot()['analysis']['state'], 'interrupted')
            self.assertEqual(restored.snapshot()['job']['state'], 'completed')

    def test_completed_recording_automatically_starts_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            rec = Recorder(directory)
            rec.start('auto-analysis-recording', .12)
            rec.thread.join(10)
            self.assertIsNotNone(rec.analysis)
            rec.wait_analysis(30)
            self.assertEqual(rec.snapshot()['analysis']['state'], 'completed')
            self.assertEqual(rec.snapshot()['analysis']['result']['mode'], 'mock')
            rec.start('stopped-analysis-recording', 600)
            rec.stop(rec.job['job_id'])
            rec.thread.join(10)
            self.assertEqual(rec.job['state'], 'stopped')
            self.assertIsNone(rec.analysis)
            with self.assertRaises(ValueError):
                rec.analyze(rec.job['job_id'], 'stopped-analysis-request')

    def test_http_analysis_and_reconnect_same_task(self):
        with tempfile.TemporaryDirectory() as directory:
            job = write_recording(directory)
            rec = Recorder(directory, auto_analyze=False)
            server = make_server(rec, '127.0.0.1', 0, 'analysis-http-token')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = f'http://127.0.0.1:{server.server_port}'
            def request(path, data=None):
                req = urllib.request.Request(base+path, data=json.dumps(data).encode() if data else None,
                    headers={'Authorization': 'Bearer analysis-http-token'})
                with opener.open(req, timeout=4) as response:
                    return json.load(response)
            try:
                with self.assertRaises(urllib.error.HTTPError):
                    request('/v1/analyze', dict(job_id='other-job', request_id='http-analysis-request'))
                first = request('/v1/analyze', dict(job_id=job['job_id'], request_id='http-analysis-request'))
                rec.wait_analysis(30)
                status = request('/v1/status')
                self.assertEqual(status['analysis']['analysis_id'], first['analysis_id'])
                self.assertEqual(status['analysis']['state'], 'completed')
                self.assertIn('candidate_analysis_v1', status['capabilities'])
                duplicate = request('/v1/analyze', dict(job_id=job['job_id'], request_id='http-analysis-request'))
                self.assertEqual(first['analysis_id'], duplicate['analysis_id'])
            finally:
                rec.wait_analysis(30)
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main(verbosity=2)
