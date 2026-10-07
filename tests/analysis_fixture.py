"""Deterministic synthetic bursts for hardware-free analysis validation."""
from pathlib import Path
import json

import h5py
import numpy as np
from linux.service import SPIKE_DTYPE, MAP_DTYPE


def write_recording(root, silent=False, layout='legacy'):
    root = Path(root)/'fixture-recording'
    root.mkdir(parents=True, exist_ok=True)
    mapping = [dict(channel=c, electrode=500+c, x=c*17.5, y=0.) for c in range(6)]
    spikes = []
    for burst in range(80):
        for ch in range(6):
            # Minority off-peak latencies supply the original algorithm's histogram background.
            jitter = ((burst*7+ch*13) % 17-8)*25 if burst % 5 == 0 else 0
            for n in range(12):
                spikes.append((20000*(burst+1)+ch*180+jitter+n*45, ch, -42.))
    spikes.sort()
    path = root/('native.raw.h5' if layout == 'wells' else 'spikes.h5')
    with h5py.File(path, 'w') as f:
        f.attrs.update(schema='spontaneous-observer-v1', complete=True, mode='mock')
        rec = f.create_group('wells/well000/rec0000' if layout == 'wells' else 'data_store/data0000')
        rec.create_dataset('settings/sampling', data=[20000])
        rec.create_dataset('settings/mapping', data=np.array([tuple(r[k] for k in MAP_DTYPE.names) for r in mapping], dtype=MAP_DTYPE))
        rec.create_dataset('spikes', data=np.array([] if silent else spikes, dtype=SPIKE_DTYPE))
    job = dict(job_id='fixture-recording', request_id='fixture-request', state='completed',
               duration_s=600, elapsed_s=600, acquired_s=81, total_spikes=0 if silent else len(spikes),
               mode='mock', mapping=mapping, sample_rate=20000, well=0,
               directory=str(root.resolve()), files=[str(path.resolve())], error='', window_s=0, first_frame=0)
    (root/'session.json').write_text(json.dumps(job), 'utf-8')
    return job
