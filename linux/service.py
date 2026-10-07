"""Independent observation service. HTTP JSON API v1 on port 8765."""
import argparse
from collections import deque
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import secrets
import shutil
import threading
import time
import uuid

import h5py
import numpy as np
from .acquisition import MockSource, MaxlabSource
from .analysis import AnalysisTask
from shared.protocol import (ACTIVE_STATES, DEFAULT_DURATION_S, DEFAULT_PORT,
                             PROTOCOL_VERSION, START_PATH, STATUS_PATH, STOP_PATH,
                             ANALYZE_PATH, ANALYSIS_STOP_PATH, ANALYSIS_ACTIVE_STATES)

ACTIVE = ACTIVE_STATES
SPIKE_DTYPE = np.dtype([('frameno', '<i8'), ('channel', '<i4'), ('amplitude', '<f4')])
MAP_DTYPE = np.dtype([('channel', '<i4'), ('electrode', '<i4'), ('x', '<f8'), ('y', '<f8')])


def write_json(path, obj):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def verify_native(paths, mapping, rate, well):
    if len(paths) != 1:
        raise RuntimeError('expected one native H5, found '+str(len(paths)))
    with h5py.File(paths[0], 'r') as f:
        key = f'wells/well{well:03d}/rec0000'
        rec = f[key] if key in f else f['data_store/data0000']
        if float(np.asarray(rec['settings/sampling']).reshape(-1)[0]) != rate:
            raise RuntimeError('native sampling rate mismatch')
        actual = {(int(r['channel']), int(r['electrode'])) for r in rec['settings/mapping'][:] if int(r['channel']) >= 0}
        if actual != {(r['channel'], r['electrode']) for r in mapping}:
            raise RuntimeError('native mapping differs from confirmed route')
        if 'spikes' not in rec:
            raise RuntimeError('native spikes dataset missing')
    return str(paths[0].resolve())


class Recorder:
    def __init__(self, root, mode='mock', config=None, auto_analyze=True, analysis_config=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.mode, self.config = mode, config or {}
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.job, self.thread = None, None
        self.events = deque()
        self.requests = {}
        self.hardware_fault = False
        self.auto_analyze = auto_analyze
        self.analysis_config = Path(analysis_config or Path(__file__).parent/'analysis.yaml')
        self.analysis = None
        # Restart never resumes recording implicitly. Preserve interrupted evidence.
        for path in sorted(self.root.glob('*/session.json'), key=lambda p: p.stat().st_mtime):
            job = json.loads(path.read_text('utf-8'))
            if job['state'] in ACTIVE:
                job.update(state='interrupted', error='服务重启：录制是否停止须现场核对')
                write_json(path, job)
                if job['mode'] == 'maxlab':
                    self.hardware_fault = True
            self.requests[job['request_id']] = job
            if job['mode'] == 'maxlab' and job['state'] in ('failed', 'interrupted'):
                self.hardware_fault = True
            self.job = job
        if self.job:
            self.analysis = AnalysisTask(self.job, self.analysis_config)

    def _persist(self):
        write_json(Path(self.job['directory'])/'session.json', self.job)

    def start(self, request_id, duration=DEFAULT_DURATION_S):
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', request_id):
            raise ValueError('invalid request_id')
        if type(duration) not in (int, float) or not math.isfinite(duration) or not 0.1 <= duration <= 600:
            raise ValueError('duration must be between .1 and 600 seconds')
        if self.mode == 'maxlab' and duration != 600:
            raise ValueError('真实录制固定为 600 秒')
        with self.lock:
            if request_id in self.requests:
                old = self.requests[request_id]
                if old['duration_s'] != duration:
                    raise ValueError('request_id already used with different duration')
                return dict(old)
            if self.hardware_fault:
                raise ValueError('硬件故障锁定：现场核对停止状态后按 README 恢复')
            if self.job and self.job['state'] in ACTIVE:
                raise ValueError('已有录制任务运行')
            if self.analysis and self.analysis.snapshot()['state'] in ANALYSIS_ACTIVE_STATES:
                raise ValueError('候选分析正在运行，请等待或取消分析后再录制')
            estimate = int(duration*self.config.get('sample_rate', 20000)*1024*2*1.3) if self.mode == 'maxlab' else 128*1024**2
            if shutil.disk_usage(self.root).free < estimate + 256*1024**2:
                raise ValueError('录制目录剩余空间不足')
            source = MaxlabSource(self.config) if self.mode == 'maxlab' else MockSource()
            jid = time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]
            directory = self.root/jid
            directory.mkdir()
            self.job = dict(job_id=jid, request_id=request_id, mode=self.mode, state='starting',
                duration_s=duration, elapsed_s=0., acquired_s=0., total_spikes=0, error='',
                directory=str(directory), files=[], mapping=source.mapping, sample_rate=source.rate,
                created_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'), window_s=0., first_frame=None,
                well=self.config.get('well', 0))
            self.analysis = None
            self.requests[request_id] = self.job
            self.events.clear()
            self.stop_event.clear()
            self._persist()
            self.thread = threading.Thread(target=self._run, args=(source, directory), daemon=False)
            self.thread.start()
            return dict(self.job)

    def analyze(self, jid, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', request_id):
            raise ValueError('invalid analysis request_id')
        with self.lock:
            if not self.job or jid != self.job['job_id']:
                raise ValueError('job_id does not match current recording')
            if self.job['state'] != 'completed':
                raise ValueError('录制保存成功后才能分析，提前停止的记录不可用')
            if self.analysis is None:
                self.analysis = AnalysisTask(self.job, self.analysis_config)
            try:
                return self.analysis.start(request_id)
            except Exception as exc:
                # Keep the recording successful even if analysis setup fails.
                with self.analysis.lock:
                    self.analysis.status.update(state='failed', message='分析启动失败，可以重试', error=str(exc))
                    self.analysis._persist()
                raise

    def cancel_analysis(self, jid, aid):
        with self.lock:
            if not self.job or jid != self.job['job_id'] or not self.analysis:
                raise ValueError('no matching analysis task')
            return self.analysis.cancel(aid)

    def wait_analysis(self, timeout=None):
        if self.analysis and self.analysis.thread:
            self.analysis.thread.join(timeout)

    def stop(self, jid):
        with self.lock:
            if not self.job or jid != self.job['job_id']:
                raise ValueError('job_id does not match current recording')
            if self.job['state'] in ACTIVE:
                self.stop_event.set()
                self.job['state'] = 'stopping'
            return dict(self.job)

    def snapshot(self):
        with self.lock:
            result = dict(protocol=PROTOCOL_VERSION, mode=self.mode, hardware_fault=self.hardware_fault, job=None,
                          capabilities=['candidate_analysis_v1'],
                          analysis=self.analysis.snapshot() if self.analysis else None)
            if not self.job:
                return result
            job = dict(self.job)
            if not self.events and job.get('preview'):
                job.update(job.pop('preview'))
                result['job'] = job
                return result
            events = list(self.events)
            counts = {}
            for _, c, _ in events:
                counts[c] = counts.get(c, 0)+1
            window = job['window_s']
            job['rates'] = [dict(**r, hz=counts.get(r['channel'], 0)/window if window else 0.) for r in job['mapping']]
            stride = max(1, math.ceil(len(events)/8000))
            job['raster'] = events[::stride]
            job['raster_total'] = len(events)
            job['raster_sampled'] = stride > 1
            result['job'] = job
            return result

    def _run(self, source, directory):
        error, paths, archive, started = '', [], None, None
        partial = directory/'spikes.partial.h5'
        try:
            if self.mode == 'maxlab':
                routing = Path(self.config['routing_path']).read_bytes()
                (directory/'routing.cfg').write_bytes(routing)
                write_json(directory/'acquisition_config.json', self.config)
                self.job['routing_sha256'] = hashlib.sha256(routing).hexdigest()
            write_json(directory/'mapping.json', source.mapping)
            archive = h5py.File(partial, 'w')
            archive.attrs.update(schema='spontaneous-observer-v1', mode=self.mode, complete=False,
                                 contains_raw_voltage=False, time_origin='first_observed_stream_frame')
            group = archive.create_group('data_store/data0000')
            group.create_dataset('settings/sampling', data=[source.rate])
            group.create_dataset('settings/mapping', data=np.array([tuple(r[k] for k in MAP_DTYPE.names) for r in source.mapping], dtype=MAP_DTYPE))
            ds = group.create_dataset('spikes', shape=(0,), maxshape=(None,), dtype=SPIKE_DTYPE, chunks=True)
            source.start(directory)
            started = source.started
            with self.lock:
                self.job.update(state='recording' if not self.stop_event.is_set() else 'stopping',
                                recording_started_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'))
                self._persist()
            first, previous, flush_at = None, None, started
            channels = {r['channel'] for r in source.mapping}
            while not self.stop_event.is_set() and time.monotonic()-started < self.job['duration_s']:
                batch = source.read(self.stop_event)
                if previous is not None and batch['first'] != previous+1:
                    raise RuntimeError('采集帧不连续')
                if first is None:
                    first = batch['first']
                previous = batch['last']
                if any(s[1] not in channels for s in batch['spikes']):
                    raise RuntimeError('Spike 通道不在确认的路由中')
                rows = np.array([tuple(s) for s in batch['spikes']], dtype=SPIKE_DTYPE)
                if len(rows):
                    n = len(ds)
                    ds.resize((n+len(rows),))
                    ds[n:] = rows
                covered = (batch['last']-first+1)/source.rate
                with self.lock:
                    self.events.extend(((f-first)/source.rate, c, a) for f, c, a in sorted(batch['spikes']))
                    # Source may order simultaneous spikes arbitrarily; normalize each batch.
                    while self.events and self.events[0][0] < covered-5:
                        self.events.popleft()
                    self.job.update(elapsed_s=min(time.monotonic()-started, self.job['duration_s']),
                                    acquired_s=covered, window_s=min(5., covered),
                                    total_spikes=len(ds), first_frame=first)
                if time.monotonic()-flush_at >= 1:
                    archive.flush()
                    with self.lock:
                        self._persist()
                    flush_at = time.monotonic()
            if previous is None and not self.stop_event.is_set():
                raise RuntimeError('未收到有效采集帧')
        except Exception as exc:
            error = str(exc)
        finally:
            with self.lock:
                self.job['state'] = 'finalizing'
            try:
                native = source.close()
                if self.mode == 'maxlab' and not error:
                    paths.append(verify_native(native, source.mapping, source.rate, source.well))
            except Exception as exc:
                error = '; '.join(filter(None, [error, '收尾失败: '+str(exc)]))
            stopped = self.stop_event.is_set()
            state = 'failed' if error else ('stopped' if stopped else 'completed')
            try:
                if archive is not None:
                    archive.attrs.update(complete=state == 'completed', state=state,
                                         acquired_s=self.job['acquired_s'], first_frame=self.job['first_frame'] or 0)
                    archive.close()
                    if not error:
                        final = directory/'spikes.h5'
                        partial.replace(final)
                        paths.append(str(final))
                    else:
                        paths.append(str(partial))
            except Exception as exc:
                state, error = 'failed', error+'; H5 收尾失败: '+str(exc)
            with self.lock:
                if state == 'failed' and self.mode == 'maxlab':
                    self.hardware_fault = True
                self.job.update(state=state, error=error, files=paths,
                                ended_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'))
                if started is not None:
                    self.job['elapsed_s'] = min(time.monotonic()-started, self.job['duration_s'])
                preview = self.snapshot()['job']
                self.job['preview'] = {k: preview[k] for k in ('rates', 'raster', 'raster_total', 'raster_sampled')}
                self._persist()
                if state == 'completed' and self.auto_analyze:
                    try:
                        self.analyze(self.job['job_id'], 'auto-'+self.job['job_id'])
                    except Exception:
                        pass  # analysis errors have their own state; recording remains completed


def make_server(recorder, host, port, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.handle_api(False)

        def do_POST(self):
            self.handle_api(True)

        def handle_api(self, post):
            if not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer '+token):
                return self.reply(401, {'error': '访问令牌不正确'})
            try:
                self.connection.settimeout(5)
                if post:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size < 4096:
                        raise ValueError('invalid request size')
                    data = json.loads(self.rfile.read(size))
                    if not isinstance(data, dict):
                        raise ValueError('expected JSON object')
                    if self.path == START_PATH:
                        result = recorder.start(data['request_id'], data.get('duration_s', DEFAULT_DURATION_S))
                    elif self.path == STOP_PATH:
                        result = recorder.stop(data['job_id'])
                    elif self.path == ANALYZE_PATH:
                        result = recorder.analyze(data['job_id'], data['request_id'])
                    elif self.path == ANALYSIS_STOP_PATH:
                        result = recorder.cancel_analysis(data['job_id'], data['analysis_id'])
                    else:
                        return self.reply(404, {'error': 'unknown command'})
                elif self.path == STATUS_PATH:
                    result = recorder.snapshot()
                else:
                    return self.reply(404, {'error': 'not found'})
                self.reply(200, result)
            except (ValueError, KeyError, TypeError) as exc:
                self.reply(400, {'error': str(exc)})
            except Exception as exc:
                self.reply(500, {'error': str(exc)})

        def reply(self, status, result):
            content = json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass
    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['mock', 'maxlab'], default='mock')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--output', default=str(Path(__file__).resolve().parents[1]/'data'/'recordings'))
    parser.add_argument('--token', required=True)
    parser.add_argument('--config')
    parser.add_argument('--analysis-config', default=str(Path(__file__).parent/'analysis.yaml'))
    parser.add_argument('--no-auto-analysis', action='store_true')
    args = parser.parse_args()
    if len(args.token) < 16:
        parser.error('token must have at least 16 characters')
    if args.mode == 'maxlab' and not args.config:
        parser.error('--mode maxlab requires --config')
    config = json.loads(Path(args.config).read_text('utf-8')) if args.config else {}
    recorder = Recorder(args.output, args.mode, config, auto_analyze=not args.no_auto_analysis,
                        analysis_config=args.analysis_config)
    server = make_server(recorder, args.host, args.port, args.token)
    print(f'Observer {args.mode}: http://{args.host}:{server.server_port} ; {recorder.root}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if recorder.job and recorder.job['state'] in ACTIVE:
            recorder.stop(recorder.job['job_id'])
        if recorder.thread:
            recorder.thread.join()
        if recorder.analysis and recorder.analysis.snapshot()['state'] in ANALYSIS_ACTIVE_STATES:
            recorder.cancel_analysis(recorder.job['job_id'], recorder.analysis.snapshot()['analysis_id'])
        recorder.wait_analysis()
        server.server_close()


if __name__ == '__main__':
    main()
