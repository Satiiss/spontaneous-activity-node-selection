"""Analyze a closed recording in a child process; stdout contains real progress."""
import argparse
import hashlib
import json
import math
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import yaml

from .spont_analysis import analyze_spontaneous_network, read_session


def emit(stage, fraction, message, **details):
    print(json.dumps(dict(stage=stage, progress=round(fraction*100, 1),
                          message=message, **details), ensure_ascii=False, allow_nan=False), flush=True)


def load_config(path):
    config = yaml.safe_load(Path(path).read_text('utf-8'))
    # Keep the original algorithm's choices; reject malformed service configuration.
    fields = dict(burst=('bin_ms', 'smooth_ms', 'threshold_z', 'min_duration_ms', 'merge_gap_ms', 'min_spikes'),
                  stable_activation=('histogram_bin_ms', 'peak_ratio_threshold'),
                  directed_connection=('delay_bin_ms', 'peak_ratio_threshold', 'min_shared_bursts', 'min_absolute_delay_ms'),
                  high_outdegree=('percentile', 'top_n', 'minimum_outdegree'))
    for section, keys in fields.items():
        for key in keys:
            value = config[section][key]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f'invalid analysis parameter {section}.{key}')
    for section, key in [('burst', 'bin_ms'), ('stable_activation', 'histogram_bin_ms'),
                         ('directed_connection', 'delay_bin_ms')]:
        if config[section][key] <= 0:
            raise ValueError(f'{section}.{key} must be positive')
    high = config['high_outdegree']
    if high['method'] not in ('percentile', 'top_n', 'absolute') or not 0 <= high['percentile'] <= 100:
        raise ValueError('invalid high_outdegree method or percentile')
    if type(high['require_stable_activation']) is not bool or high['top_n'] < 1:
        raise ValueError('invalid high_outdegree settings')
    return {key: config[key] for key in fields}


def records(frame):
    return json.loads(frame.to_json(orient='records'))


def analyze_file(path, output, config, mode, well=0, expected_mapping=None, expected_rate=None):
    path, output = Path(path).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    emit('reading', .02, '读取录制文件中的 Spike 和电极映射')
    with h5py.File(path, 'r') as f:
        if f.attrs.get('schema') == 'spontaneous-observer-v1' and not f.attrs.get('complete', False):
            raise ValueError('录制未完成，不分析部分文件')
    session = read_session(path, well=well)
    if not math.isfinite(session.sampling_hz) or session.sampling_hz <= 0:
        raise ValueError('invalid sampling rate')
    if expected_rate is not None and session.sampling_hz != expected_rate:
        raise ValueError('分析文件采样率与录制会话不一致')
    if expected_mapping is not None:
        actual = {c: e for c, e in session.channel_to_electrode.items() if c >= 0}
        expected = {row['channel']: row['electrode'] for row in expected_mapping}
        if actual != expected:
            raise ValueError('分析文件映射与录制会话不一致')
    if not set(session.channel).issubset(session.channel_to_electrode):
        raise ValueError('Spike 通道缺少真实电极映射')
    if not np.isfinite(session.amplitude).all() or (session.frameno < 0).any():
        raise ValueError('invalid spike data')
    emit('reading', .10, 'Spike 读取完成', spike_count=len(session.channel),
         active_channels=len(np.unique(session.channel)), mapping_count=len(session.channel_to_electrode))
    # The original core requires at least one active channel. Silence is a valid empty result.
    if len(session.channel):
        result = analyze_spontaneous_network(session, config, progress=emit)
    else:
        result = dict(bursts=[], burst_table=pd.DataFrame(columns=['burst_id', 'start_sec', 'end_sec', 'duration_ms', 'spike_count']),
            activation=pd.DataFrame(columns=['channel', 'electrode', 'stable_activation']),
            edges=pd.DataFrame(columns=['source_channel', 'target_channel', 'source_electrode', 'target_electrode']),
            electrodes=pd.DataFrame(columns=['channel', 'electrode', 'x_um', 'y_um', 'out_degree', 'in_degree', 'out_minus_in', 'high_outdegree']),
            high_outdegree_threshold=float('nan'))
        emit('selection', .80, '记录无 Spike，候选为空', burst_count=0, edge_count=0)
    emit('saving', .90, '保存分析表和候选结果')
    for key in ('electrodes', 'edges', 'burst_table', 'activation'):
        result[key].to_csv(output/(key+'.csv'), index=False, encoding='utf-8-sig')
    candidates = records(result['electrodes'][result['electrodes']['high_outdegree'].astype(bool)])
    threshold = float(result['high_outdegree_threshold'])
    threshold = threshold if math.isfinite(threshold) else None
    hashes = {name: hashlib.sha256((Path(__file__).parent/'spont_analysis'/name).read_bytes()).hexdigest()
              for name in ('core.py', 'burst.py', 'io.py')}
    summary = dict(schema='spontaneous-candidates-v1', mode=mode, source_h5=str(path),
        algorithm='spont_burst_outdegree', config=config, algorithm_sha256=hashes,
        spike_count=len(session.channel), active_channels=len(np.unique(session.channel)),
        mapping_count=len(session.channel_to_electrode), burst_count=len(result['bursts']),
        edge_count=len(result['edges']), candidate_count=len(candidates), threshold=threshold,
        candidates=candidates, electrodes=records(result['electrodes']),
        mapping=[dict(channel=ch, electrode=ele, x=session.channel_positions_um[ch][0],
                      y=session.channel_positions_um[ch][1])
                 for ch, ele in session.channel_to_electrode.items()
                 if ch >= 0 and ch in session.channel_positions_um],
        burst_preview=records(result['burst_table'].head(500)),
        files=[str(output/(key+'.csv')) for key in ('electrodes', 'edges', 'burst_table', 'activation')],
        note='自发先后关系用于筛选候选，未进行刺激标定。')
    (output/'config_used.yaml').write_text(yaml.safe_dump(config, allow_unicode=True), 'utf-8')
    # result.json is the completion marker; partial CSVs must never appear as a successful result.
    tmp = output/'result.tmp'
    tmp.write_text(json.dumps(summary, ensure_ascii=False, allow_nan=False, indent=2), 'utf-8')
    tmp.replace(output/'result.json')
    emit('completed', 1., '分析完成', candidate_count=len(candidates), threshold=threshold)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--mode', choices=['mock', 'maxlab', 'file'], required=True)
    parser.add_argument('--session')
    args = parser.parse_args()
    if args.mode == 'file':
        if args.session:
            parser.error('file mode does not use a recording manifest')
        analyze_file(args.input, args.output, load_config(args.config), 'file')
        return
    if not args.session:
        parser.error('recording analysis requires --session')
    job = json.loads(Path(args.session).read_text('utf-8'))
    if job['state'] != 'completed':
        raise ValueError('录制必须完成后才能分析')
    analyze_file(args.input, args.output, load_config(args.config), args.mode,
                 well=job.get('well', 0), expected_mapping=job['mapping'], expected_rate=job['sample_rate'])


if __name__ == '__main__':
    main()
