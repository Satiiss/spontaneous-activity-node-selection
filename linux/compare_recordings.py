"""Read-only native/stream spike comparison; never opens the hardware SDK."""
import argparse
import json
import math
from pathlib import Path

import h5py
import numpy as np


def segment(f, well):
    key = f'wells/well{well:03d}/rec0000'
    if key in f:
        return f[key]
    if 'data_store/data0000' in f:
        return f['data_store/data0000']
    raise ValueError('Cannot find the fixed recording segment for the selected well')


def read_file(path, well):
    with h5py.File(path, 'r') as f:
        rec = segment(f, well)
        rate = float(np.asarray(rec['settings/sampling']).reshape(-1)[0])
        mapping = {(int(r['channel']), int(r['electrode']))
                   for r in rec['settings/mapping'][:] if int(r['channel']) >= 0}
        events = rec['spikes'][:]
        if not {'frameno', 'channel', 'amplitude'} <= set(events.dtype.names or ()):
            raise ValueError('Missing spike fields')
        if (events['frameno'] < 0).any() or not np.isfinite(events['amplitude']).all():
            raise ValueError('Invalid spike values')
        if (events['channel'] < 0).any() or (events['channel'] >= 1024).any():
            raise ValueError('Invalid spike channel')
        raw = []
        if 'groups' in rec:
            for name, group in rec['groups'].items():
                item = {'group': name}
                for key in ('raw', 'frame_nos'):
                    if key in group:
                        ds = group[key]
                        item[key + '_shape'] = list(ds.shape)
                        if key == 'frame_nos' and ds.size:
                            if ds.ndim == 1:
                                item['first_frame_nos'] = ds[:min(4, len(ds))].tolist()
                                item['last_frame_nos'] = ds[max(0, len(ds)-4):].tolist()
                            elif ds.size <= 8:
                                item['frame_nos'] = ds[()].tolist()
                raw.append(item)
        attrs = {key: f.attrs[key].item() if hasattr(f.attrs[key], 'item') else f.attrs[key]
                 for key in ('complete', 'first_frame', 'acquired_s') if key in f.attrs}
        excluded = len(rec['unmapped_spikes']) if 'unmapped_spikes' in rec else 0
    return rate, mapping, events, attrs, raw, excluded


def nearest_deltas(query, reference):
    """Signed reference minus query, nearest same-channel event, not one-to-one."""
    if not len(query) or not len(reference):
        return np.array([], dtype=np.int64)
    query, reference = np.sort(query), np.sort(reference)
    indexes = np.searchsorted(reference, query)
    left = reference[np.clip(indexes - 1, 0, len(reference)-1)] - query
    right = reference[np.clip(indexes, 0, len(reference)-1)] - query
    return np.where(np.abs(left) <= np.abs(right), left, right)


def compare(directory):
    directory = Path(directory).resolve()
    job = json.loads((directory/'session.json').read_text('utf-8'))
    if job['state'] != 'completed' or job['mode'] != 'maxlab':
        raise ValueError('Use a completed real recording session')
    native = list(directory.glob('native*.h5'))
    if len(native) != 1:
        raise ValueError('Expected exactly one native H5')
    well = job.get('well', 0)
    nr, nm, ne, _, raw, _ = read_file(native[0], well)
    sr, sm, se, attrs, _, quarantined = read_file(directory/'spikes.h5', well)
    if nr != sr or sr != job['sample_rate'] or not math.isfinite(sr) or sr <= 0:
        raise ValueError('Sampling rate mismatch')
    expected = {(r['channel'], r['electrode']) for r in job['mapping']}
    if nm != sm or sm != expected:
        raise ValueError('Native, stream and session electrode mappings differ')
    if attrs.get('complete') is not True:
        raise ValueError('Stream H5 is incomplete')
    first = int(attrs['first_frame'])
    span = round(float(attrs['acquired_s'])*sr)
    if span <= 0:
        raise ValueError('Invalid stream coverage')
    last = first + span - 1
    # Compare absolute acquisition timestamps, never align to first spikes.
    channels = sorted(c for c, _ in sm)
    native_unmapped = int(np.count_nonzero(~np.isin(ne['channel'], channels)))
    ne = ne[np.isin(ne['channel'], channels)]
    if not np.isin(se['channel'], channels).all():
        raise ValueError('Stream spike dataset contains unmapped channels')
    native_total = len(ne)
    ne = ne[(ne['frameno'] >= first) & (ne['frameno'] <= last)]
    if ((se['frameno'] < first) | (se['frameno'] > last)).any():
        raise ValueError('Stream events outside saved acquisition window')
    ne = ne[np.argsort(ne['channel'], kind='stable')]
    se = se[np.argsort(se['channel'], kind='stable')]
    rows, forward, reverse = [], [], []
    for ch in channels:
        a, b = np.searchsorted(ne['channel'], [ch, ch+1])
        n = ne['frameno'][a:b].astype(np.int64)
        a, b = np.searchsorted(se['channel'], [ch, ch+1])
        s = se['frameno'][a:b].astype(np.int64)
        delta = nearest_deltas(n, s)
        rev = nearest_deltas(s, n)
        forward.append(delta)
        reverse.append(rev)
        rows.append(dict(channel=ch, native_spikes=len(n), stream_spikes=len(s),
                         stream_minus_native=len(s)-len(n)))
    forward = np.concatenate(forward)
    reverse = np.concatenate(reverse)
    def statistics(delta, denominator):
        if not denominator:
            return {'events': 0}
        result = {'events': denominator, 'events_with_reference_channel': len(delta)}
        for frames in (0, 1, round(sr*.001), round(sr*.005)):
            result[f'nearest_within_{frames}_frames_fraction'] = float(np.count_nonzero(np.abs(delta) <= frames)/denominator)
        if len(delta):
            result['signed_delta_ms_p05_p50_p95'] = (np.percentile(delta, [5, 50, 95])*1000/sr).tolist()
            result['absolute_delta_ms_p50_p95_p99'] = (np.percentile(np.abs(delta), [50, 95, 99])*1000/sr).tolist()
        return result
    return dict(session=str(directory), sample_rate=sr, mapping_channels=len(sm),
                stream_window_first_frame=first, stream_window_last_frame=last,
                stream_coverage_s=span/sr, requested_duration_s=job['duration_s'],
                native_mapped_spikes_total=native_total, native_mapped_spikes_in_stream_window=len(ne),
                stream_mapped_spikes=len(se), native_unmapped_spikes=native_unmapped,
                stream_quarantined_spikes=quarantined,
                native_raw_metadata=raw,
                native_to_stream=statistics(forward, len(ne)), stream_to_native=statistics(reverse, len(se)),
                largest_channel_count_differences=sorted(rows, key=lambda r: abs(r['stream_minus_native']), reverse=True)[:20],
                channels=rows,
                limits=['Nearest-event comparison is not one-to-one and does not measure detection accuracy.',
                        'Comparison uses the stream acquisition window; native coverage needs separate verification from raw metadata.',
                        'Agreement does not prove spikes are neuronal; inspect raw voltage and noise separately.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', required=True)
    args = parser.parse_args()
    report = compare(args.session)
    output = Path(args.session)/'quality-comparison.json'
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)+'\n', 'utf-8')
    print('采样率：', report['sample_rate'], 'Hz；映射通道：', report['mapping_channels'])
    print('实时流覆盖：', report['stream_coverage_s'], '秒；目标：', report['requested_duration_s'], '秒')
    print('同一实时流时间范围内，官方/实时映射 Spike：',
          report['native_mapped_spikes_in_stream_window'], '/', report['stream_mapped_spikes'])
    print('官方未映射事件 / 实时单独保存事件：', report['native_unmapped_spikes'], '/', report['stream_quarantined_spikes'])
    print('时间差方向：官方→实时 = 实时时间戳减官方时间戳；以下为同通道最近事件对比，非一对一匹配。')
    print('官方→实时：', json.dumps(report['native_to_stream'], ensure_ascii=False))
    print('实时→官方：', json.dumps(report['stream_to_native'], ensure_ascii=False))
    print('官方原始数据元信息：', json.dumps(report['native_raw_metadata'], ensure_ascii=False))
    print('数量差异最大的前5通道：', json.dumps(report['largest_channel_count_differences'][:5]))
    print('报告：', output.resolve())


if __name__ == '__main__':
    main()
