import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import h5py
import numpy as np

from linux.compare_recordings import compare, nearest_deltas
from linux.service import SPIKE_DTYPE, MAP_DTYPE


class ComparisonTests(unittest.TestCase):
    def fixture(self, folder, silent=False, wrong_mapping=False, incomplete=False):
        folder = Path(folder)
        mapping = [dict(channel=c, electrode=10+c, x=0., y=0.) for c in range(2)]
        (folder/'session.json').write_text(json.dumps(dict(state='completed', mode='maxlab',
            well=0, mapping=mapping, sample_rate=1000, duration_s=600)), 'utf-8')
        paths = [folder/'native.raw.h5', folder/'spikes.h5']
        native = [(900, 0, -42.), (1000, 0, -42.), (1010, 0, -42.),
                  (1050, 1, -42.), (1051, 355, -9.), (1150, 1, -42.)]
        stream = [(1001, 0, -42.), (1011, 0, -42.), (1051, 1, -42.)]
        for index, path in enumerate(paths):
            with h5py.File(path, 'w') as f:
                if index:
                    f.attrs.update(complete=not incomplete, first_frame=1000, acquired_s=.1)
                g = f.create_group('wells/well000/rec0000' if index == 0 else 'data_store/data0000')
                g.create_dataset('settings/sampling', data=[1000])
                g.create_dataset('settings/mapping', data=np.array([
                    (c, 100 if wrong_mapping and index == 0 else 10+c, 0., 0.) for c in range(2)], dtype=MAP_DTYPE))
                g.create_dataset('spikes', data=np.array([] if silent else (stream if index else native), dtype=SPIKE_DTYPE))
                if index:
                    g.create_dataset('unmapped_spikes', data=np.array([(1051, 355, -9.)], dtype=SPIKE_DTYPE))
                else:
                    raw = g.create_group('groups/routed_channels')
                    raw.create_dataset('raw', shape=(2, 200), dtype='i2')
                    raw.create_dataset('frame_nos', data=[900])
        return paths

    def test_absolute_window_counts_and_one_frame_offset_read_only(self):
        with tempfile.TemporaryDirectory() as root:
            paths = self.fixture(root)
            hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
            report = compare(root)
            self.assertEqual(report['native_mapped_spikes_total'], 5)
            self.assertEqual(report['native_mapped_spikes_in_stream_window'], 3)
            self.assertEqual(report['stream_mapped_spikes'], 3)
            self.assertEqual(report['native_unmapped_spikes'], 1)
            self.assertEqual(report['stream_quarantined_spikes'], 1)
            self.assertEqual(report['stream_coverage_s'], .1)
            self.assertEqual(report['native_to_stream']['signed_delta_ms_p05_p50_p95'], [1., 1., 1.])
            self.assertEqual(report['native_to_stream']['nearest_within_0_frames_fraction'], 0)
            self.assertEqual(report['native_to_stream']['nearest_within_1_frames_fraction'], 1)
            self.assertEqual(report['stream_to_native']['signed_delta_ms_p05_p50_p95'], [-1., -1., -1.])
            self.assertEqual(report['native_raw_metadata'][0]['raw_shape'], [2, 200])
            self.assertEqual([hashlib.sha256(p.read_bytes()).hexdigest() for p in paths], hashes)

    def test_mapping_mismatch_and_incomplete_stream_rejected(self):
        for kwargs in ({'wrong_mapping': True}, {'incomplete': True}):
            with tempfile.TemporaryDirectory() as root:
                self.fixture(root, **kwargs)
                with self.assertRaises(ValueError):
                    compare(root)

    def test_silent_recording_has_no_invented_match(self):
        with tempfile.TemporaryDirectory() as root:
            self.fixture(root, silent=True)
            report = compare(root)
            self.assertEqual(report['native_to_stream'], {'events': 0})
            self.assertEqual(report['stream_to_native'], {'events': 0})
            json.dumps(report, allow_nan=False)

    def test_missing_reference_channel_counts_against_match_fraction(self):
        with tempfile.TemporaryDirectory() as root:
            paths = self.fixture(root)
            with h5py.File(paths[1], 'r+') as f:
                g = f['data_store/data0000']
                data = g['spikes'][:]
                del g['spikes']
                g.create_dataset('spikes', data=data[data['channel'] == 0])
            report = compare(root)
            self.assertAlmostEqual(report['native_to_stream']['nearest_within_1_frames_fraction'], 2/3)
            self.assertEqual(report['native_to_stream']['events_with_reference_channel'], 2)

    def test_nearest_event_is_explicitly_not_one_to_one(self):
        np.testing.assert_array_equal(nearest_deltas(np.array([100, 101]), np.array([100])), [0, -1])
