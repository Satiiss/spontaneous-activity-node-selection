"""Verify vendor layout and receiver behavior without glove/network equipment."""
from ctypes import POINTER, cast, sizeof
import threading
import unittest

from windows.mhand_sdk import MocapData
from windows.glove_worker import receive


class FakeSDK:
    def __init__(self, frames, stop, fail=False):
        self.frames, self.stop, self.fail = list(frames), stop, fail
        self.cleaned = []

    def UdpOpen(self, index, port):
        return True

    def UdpSendRequestConnect(self, index, host, port):
        return not self.fail

    def UdpRecvMocapData(self, index, host, port, pointer):
        if not self.frames:
            self.stop.set()
            return False
        data = cast(pointer, POINTER(MocapData)).contents
        left, right = self.frames.pop(0)
        data.isUpdate, data.frameIndex, data.frequency = True, 7, 60
        data.gestureResultL, data.gestureResultR = left, right
        return True

    def UdpRemove(self, *args):
        self.cleaned.append('remove')

    def UdpClose(self, *args):
        self.cleaned.append('close')


class ReceiverTests(unittest.TestCase):
    def test_vendor_layout(self):
        self.assertEqual(sizeof(MocapData), 4656)
        self.assertEqual(MocapData.gestureResultR.offset, MocapData.gestureResultL.offset+4)

    def test_left_right_and_cleanup(self):
        for hand, expected in [('left', [16, 2]), ('right', [5, 3])]:
            stop, events = threading.Event(), []
            sdk = FakeSDK([(16, 5), (2, 3)], stop)
            receive(sdk, '127.0.0.1', 7000, hand, stop, events.append)
            self.assertTrue(events[0]['connected'])
            self.assertEqual([e['gesture_id'] for e in events if e['type'] == 'frame'], expected)
            self.assertEqual(sdk.cleaned, ['remove', 'close'])

    def test_failed_connection_cleanup(self):
        stop = threading.Event()
        sdk = FakeSDK([], stop, fail=True)
        with self.assertRaises(RuntimeError):
            receive(sdk, '127.0.0.1', 7000, 'left', stop)
        self.assertEqual(sdk.cleaned, ['remove', 'close'])

    def test_timeout_without_frames(self):
        class SilentSDK(FakeSDK):
            def UdpRecvMocapData(self, *args):
                return False
        stop = threading.Event()
        sdk = SilentSDK([], stop)
        times = iter([0, 4])
        with self.assertRaises(TimeoutError):
            receive(sdk, '127.0.0.1', 7000, 'left', stop, clock=lambda: next(times))
        self.assertEqual(sdk.cleaned, ['remove', 'close'])

    def test_data_loss_after_connection(self):
        class LostSDK(FakeSDK):
            def UdpRecvMocapData(self, *args):
                return super().UdpRecvMocapData(*args) if self.frames else False
        stop, events = threading.Event(), []
        sdk = LostSDK([(16, 5)], stop)
        times = iter([0, .1, 3])
        with self.assertRaises(TimeoutError):
            receive(sdk, '127.0.0.1', 7000, 'left', stop, events.append, clock=lambda: next(times))
        self.assertTrue(events[0]['connected'])
        self.assertEqual(sdk.cleaned, ['remove', 'close'])


if __name__ == '__main__':
    unittest.main()
