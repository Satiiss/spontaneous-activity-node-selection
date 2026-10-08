"""Exercise the compiled hardware path with synthetic SDK frames, no equipment."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from linux.reader_protocol import batches

SDK = r'''
#pragma once
#include <cstdint>
#include <cstdlib>
#include <string>
namespace maxlab {
enum Status { MAXLAB_OK, MAXLAB_NO_FRAME };
enum class FilterType { IIR, FIR };
struct SpikeEvent { unsigned long frameNo; float amp; uint16_t channel; unsigned char wellId; };
struct FrameInfo { uint64_t frame_number; uint8_t well_id; bool corrupted; };
struct FilteredFrameData { uint64_t spikeCount; const SpikeEvent* spikeEvents; FrameInfo frameInfo; };
inline void checkVersions() {}
inline Status DataStreamerFiltered_open(FilterType) { return MAXLAB_OK; }
inline Status DataStreamerFiltered_close() { return MAXLAB_OK; }
inline Status DataStreamerFiltered_receiveNextFrame(FilteredFrameData* data) {
    static uint64_t call = 0;
    static SpikeEvent events[1024];
    const std::string scenario = std::getenv("READER_CASE") ? std::getenv("READER_CASE") : "delay";
    uint64_t frame = 1000 + call++;
    if (scenario == "gap" && call == 2) ++frame;
    data->frameInfo = {frame, 0, scenario == "corrupt"};
    events[0] = {frame, -42.0f, 1, 0};
    events[1] = {frame - 1, -43.0f, 2, 0};
    data->spikeCount = scenario == "silent" ? 0 : 2;
    if (scenario == "ontime") data->spikeCount = 1;
    if (scenario == "well") events[1].wellId = 1;
    if (scenario == "old") events[1].frameNo = frame - 2;
    if (scenario == "future") events[1].frameNo = frame + 1;
    if (scenario == "dense") {
        data->spikeCount = 1024;
        // Adjacent receive frames attribute all events to one peak frame.
        uint64_t peak = call % 2 ? frame : frame - 1;
        for (unsigned i = 0; i < 1024; ++i) events[i] = {peak, -42.0f, static_cast<uint16_t>(i), 0};
    }
    data->spikeEvents = events;
    return MAXLAB_OK;
}
}
'''

@unittest.skipUnless(shutil.which("g++"), "g++ required; compiled on Linux CI")
class ReaderSdkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        folder = Path(cls.temp.name)
        (folder / "maxlab").mkdir()
        (folder / "maxlab/maxlab.h").write_text(SDK)
        cls.exe = folder / "reader"
        source = Path(__file__).resolve().parents[1] / "linux/reader/reader.cpp"
        subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-DWITH_MAXLAB", "-I", str(folder), str(source), "-o", str(cls.exe)],
                       check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_reader(self, scenario):
        import os
        return subprocess.run([str(self.exe), "--mode", "maxlab", "--sample-rate", "20000",
                               "--frames", "203"], env={**os.environ, "READER_CASE": scenario},
                              capture_output=True, text=True, timeout=10)

    def test_delayed_boundary_and_final_frame_preserve_timestamps(self):
        result = self.run_reader("delay")
        self.assertEqual(result.returncode, 0, result.stderr)
        records = list(batches(result.stdout.splitlines()))
        self.assertEqual([(b["first"], b["last"]) for _, b in records], [(1000, 1199), (1200, 1202)])
        events = [event for _, b in records for event in b["spikes"]]
        self.assertEqual(len(events), 406)
        self.assertEqual([(f, c) for f, c, _ in events],
                         [(f, c) for f in range(1000, 1203) for c in (1, 2)])
        self.assertIn("before the first acquired frame", result.stderr)

    def test_ontime_and_silent_stream(self):
        for case, size in (("ontime", 203), ("silent", 0)):
            with self.subTest(case=case):
                result = self.run_reader(case)
                self.assertEqual(result.returncode, 0, result.stderr)
                records = list(batches(result.stdout.splitlines()))
                self.assertEqual(sum(len(b["spikes"]) for _, b in records), size)

    def test_dense_events_remain_within_protocol_batch_bound(self):
        result = self.run_reader("dense")
        self.assertEqual(result.returncode, 0, result.stderr)
        records = list(batches(result.stdout.splitlines()))
        self.assertEqual(sum(b["last"] - b["first"] + 1 for _, b in records), 203)
        self.assertEqual(sum(len(b["spikes"]) for _, b in records), 2048 * 102)
        self.assertTrue(all(len(b["spikes"]) <= 4096 for _, b in records))

    def test_bad_well_timestamp_gap_and_corruption_still_fail(self):
        for case in ("well", "old", "future", "gap", "corrupt"):
            with self.subTest(case=case):
                result = self.run_reader(case)
                self.assertNotEqual(result.returncode, 0)
                with self.assertRaises(ValueError):
                    list(batches(result.stdout.splitlines()))
