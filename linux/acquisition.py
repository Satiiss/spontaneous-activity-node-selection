"""Passive acquisition only. No initialization, routing downloads or stimulation."""
import importlib
import json
import math
from pathlib import Path
import queue
import random
import subprocess
import threading
import time

from .reader_protocol import batches


def validate_mapping(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1024:
        raise ValueError('mapping must contain 1..1024 channels')
    channels, electrodes = set(), set()
    for r in rows:
        c, e = r['channel'], r['electrode']
        if (type(c) is not int or not 0 <= c < 1024 or type(e) is not int or e < 0
                or c in channels or e in electrodes
                or not all(math.isfinite(float(r[k])) for k in ('x', 'y'))):
            raise ValueError('invalid or duplicate electrode mapping')
        channels.add(c)
        electrodes.add(e)
    return sorted(rows, key=lambda r: r['channel'])


class MockSource:
    rate = 20000

    def __init__(self):
        self.mapping = [dict(channel=c, electrode=1000+c, x=(c % 32)*17.5,
                             y=(c//32)*17.5) for c in range(1024)]
        self.rng = random.Random()
        self.frame = 0

    def start(self, directory):
        self.started = time.monotonic()

    def read(self, stop):
        stop.wait(.05)
        last = max(self.frame, int((time.monotonic()-self.started)*self.rate))
        spikes = []
        # Synthetic variable activity, including truly silent channels.
        for c in range(1024):
            if c % 19 == 0:
                continue
            hz = .4 + 6 * (1 + math.sin(c*.13)) / 2
            hz *= 1 + 2 * max(0, math.sin(last/self.rate*2))**12
            expected = hz*(last-self.frame+1)/self.rate
            count = int(expected) + int(self.rng.random() < expected % 1)
            spikes.extend([self.rng.randint(self.frame, last), c, -42.] for _ in range(count))
        result = dict(first=self.frame, last=last, spikes=sorted(spikes))
        self.frame = last+1
        return result

    def close(self):
        return []


class MaxlabSource:
    def __init__(self, cfg):
        required = ('prepared_fixed_routing', 'sample_rate_verified', 'exclusive_saving_confirmed')
        if not all(cfg.get(k) is True for k in required):
            raise ValueError('现场须确认固定路由、采样率及独占录制，见 hardware.example.json')
        self.cfg = cfg
        self.rate = cfg['sample_rate']
        if type(self.rate) is not int or not 0 < self.rate <= 1000000:
            raise ValueError('invalid sample rate')
        self.well = cfg['well']
        if type(self.well) is not int or not 0 <= self.well <= 255:
            raise ValueError('invalid well')
        if cfg['filter'] not in ('iir', 'fir'):
            raise ValueError('invalid filter')
        self.mapping = validate_mapping(json.loads(Path(cfg['mapping_path']).read_text('utf-8')))
        if not Path(cfg['reader_path']).is_file():
            raise ValueError('build the Linux SDK reader first')
        if not Path(cfg['routing_path']).is_file():
            raise ValueError('routing snapshot not found')
        self.process = self.saver = self.log = None
        self.opened = self.recording = False
        self.queue = queue.Queue(maxsize=512)
        self.halt = threading.Event()

    def start(self, directory):
        self.directory = directory
        mx = importlib.import_module('maxlab')
        self.saver = mx.Saving()
        self.saver.open_directory(str(directory.resolve()))
        self.saver.set_legacy_format(False)
        self.opened = True  # cleanup even if SDK call partly succeeds then raises
        self.saver.start_file('native')
        self.saver.group_delete_all()
        result = self.saver.group_define(self.well, 'routed_channels', [r['channel'] for r in self.mapping])
        if str(result).lower() != 'ok':
            raise RuntimeError('MaxLab recording group rejected: '+str(result))
        self.recording = True
        self.saver.start_recording([self.well])
        self.started = time.monotonic()
        self.log = (directory/'reader.log').open('w', encoding='utf-8')
        self.process = subprocess.Popen([self.cfg['reader_path'], '--mode', 'maxlab',
            '--sample-rate', str(self.rate), '--well', str(self.well),
            '--filter', self.cfg['filter'], '--frames', '0'], stdout=subprocess.PIPE,
            stderr=self.log, text=True, encoding='utf-8')
        self.thread = threading.Thread(target=self._receive, daemon=True)
        self.thread.start()

    def _receive(self):
        def put(item):
            while not self.halt.is_set():
                try:
                    self.queue.put(item, timeout=.1)
                    return
                except queue.Full:
                    pass
        try:
            for header, batch in batches(self.process.stdout):
                if self.halt.is_set():
                    return
                if any(header[k] != v for k, v in dict(mode='maxlab', sample_rate=self.rate,
                                                       well=self.well, filter=self.cfg['filter']).items()):
                    raise ValueError('reader header mismatch')
                put(batch)
            put(RuntimeError('device stream ended unexpectedly'))
        except Exception as exc:
            put(exc)

    def read(self, stop):
        try:
            item = self.queue.get(timeout=3)
        except queue.Empty:
            raise TimeoutError('3 秒未收到设备数据')
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        errors = []
        # Stop official recording before shutting down the observation stream.
        if self.recording:
            try:
                self.saver.stop_recording()
            except Exception as exc:
                errors.append('stop_recording: '+str(exc))
            self.recording = False
        if self.opened:
            try:
                self.saver.stop_file()
            except Exception as exc:
                errors.append('stop_file: '+str(exc))
            self.opened = False
        self.halt.set()
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
                errors.append('reader did not stop gracefully')
            if self.process.returncode != 0:
                errors.append('reader exit: '+str(self.process.returncode))
            self.thread.join(timeout=4)
            self.process.stdout.close()
        if self.log:
            self.log.close()
        if errors:
            raise RuntimeError('; '.join(errors))
        return list(self.directory.glob('native*.h5'))
