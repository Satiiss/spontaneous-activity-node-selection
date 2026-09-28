"""Consume mea_reader JSONL; never substitute host time for acquisition frames."""
import argparse
import json
import math
import subprocess
import sys


def batches(lines):
    """Validate a single stream session. EOF without end is a failure."""
    hello = None
    previous = None
    total = 0
    ended = False
    for line in lines:
        if len(line) > 1024 * 1024:
            raise ValueError("oversized stream record")
        record = json.loads(line)
        kind = record.get("type")
        if ended:
            raise ValueError("data after end")
        if hello is None:
            if (kind != "hello" or record.get("version") != 1
                    or record.get("mode") not in ("mock", "maxlab")
                    or record.get("filter") not in ("iir", "fir")
                    or type(record.get("sample_rate")) is not int
                    or not 0 < record["sample_rate"] <= 1000000
                    or type(record.get("well")) is not int
                    or not 0 <= record["well"] <= 255):
                raise ValueError("invalid stream header")
            hello = record
        elif kind == "batch":
            first, last = record["first"], record["last"]
            if (type(first) is not int or type(last) is not int or first < 0
                    or last < first or last - first >= 200
                    or (previous is not None and first != previous + 1)):
                raise ValueError("frame discontinuity")
            spikes = record["spikes"]
            if not isinstance(spikes, list) or len(spikes) > 5119:
                raise ValueError("invalid spike batch")
            for frame, channel, amplitude in spikes:
                if (type(frame) is not int or not first <= frame <= last
                        or type(channel) is not int or not 0 <= channel < 1024
                        or not isinstance(amplitude, (float, int)) or not math.isfinite(amplitude)):
                    raise ValueError("invalid spike")
            previous = last
            total += last - first + 1
            yield hello, record
        elif kind == "end":
            if record.get("frames") != total:
                raise ValueError("frame count mismatch")
            ended = True
        else:
            raise ValueError("unexpected stream record")
    if not ended:
        raise ValueError("stream ended without completion")


class ResponseWindow:
    """Feed validated batches from one session with an independently verified t0.

    channel_to_electrode must describe the active, fixed routing. t0 must be an
    acquisition frame in the SAME session, not seq.send() time. This class does
    not discover t0 or compensate for SDK filtering delay.
    """
    def __init__(self, stimulus_frame, sample_rate, channel_to_electrode,
                 minimum_ms=3.0, maximum_ms=200.0):
        if (type(stimulus_frame) is not int or stimulus_frame < 0
                or not math.isfinite(sample_rate) or sample_rate <= 0
                or not 0 <= minimum_ms <= maximum_ms <= 200):
            raise ValueError("invalid window")
        if (not channel_to_electrode
                or any(type(c) is not int or not 0 <= c < 1024
                       or type(e) is not int or e < 0 for c, e in channel_to_electrode.items())
                or len(set(channel_to_electrode.values())) != len(channel_to_electrode)):
            raise ValueError("invalid routing map")
        self.t0 = stimulus_frame
        self.rate = sample_rate
        self.start = stimulus_frame + math.ceil(minimum_ms * sample_rate / 1000)
        self.end = stimulus_frame + math.floor(maximum_ms * sample_rate / 1000)
        self.mapping = dict(channel_to_electrode)
        self.latencies = {}
        self.previous = None
        self.complete = False

    def feed(self, batch):
        if self.complete:
            raise ValueError("window already complete")
        if self.previous is None:
            if batch["first"] > self.t0:
                raise ValueError("stream started after stimulus")
        elif batch["first"] != self.previous + 1:
            raise ValueError("gap in response window")
        self.previous = batch["last"]
        for frame, channel, _ in batch["spikes"]:
            electrode = self.mapping.get(channel)
            if electrode is not None and self.start <= frame <= self.end:
                latency = (frame - self.t0) * 1000 / self.rate
                self.latencies[electrode] = min(latency, self.latencies.get(electrode, math.inf))
        self.complete = self.previous >= self.end
        return dict(self.latencies) if self.complete else None


def main():
    parser = argparse.ArgumentParser(description="Launch C++ reader and validate its stream")
    parser.add_argument("--reader", required=True)
    parser.add_argument("--mode", choices=["mock", "maxlab"], default="mock")
    parser.add_argument("--sample-rate", type=int)
    parser.add_argument("--frames", type=int, default=20000)
    parser.add_argument("--well", type=int, default=0)
    parser.add_argument("--filter", choices=["iir", "fir"], default="iir")
    args = parser.parse_args()
    if args.mode == "maxlab" and args.sample_rate is None:
        parser.error("maxlab requires a verified --sample-rate")
    command = [args.reader, "--mode", args.mode, "--frames", str(args.frames),
               "--sample-rate", str(args.sample_rate or 20000), "--well", str(args.well),
               "--filter", args.filter]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, text=True, encoding="utf-8")
    count = spikes = 0
    try:
        for header, batch in batches(process.stdout):
            count += batch["last"] - batch["first"] + 1
            spikes += len(batch["spikes"])
            if count % header["sample_rate"] == 0:
                print(json.dumps(dict(mode=header["mode"], frames=count, spikes=spikes)), flush=True)
        if process.wait(timeout=5):
            raise RuntimeError("reader exited with an error; discard active trial")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()
    print(json.dumps(dict(status="complete", mode=args.mode, frames=count, spikes=spikes)))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        sys.exit(str(exc))
