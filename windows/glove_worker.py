"""Isolated real mHand receiver. This module has no Maxwell calls."""
import argparse
from ctypes import byref
import json
import sys
import threading
import time

from .mhand_sdk import MocapData, load_sdk


def emit(event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def receive(sdk, host, port, hand, stop, publish=emit, clock=time.monotonic):
    opened = False
    address = host.encode('ascii')
    try:
        opened = bool(sdk.UdpOpen(0, 0))
        if not opened:
            raise RuntimeError('无法打开本地 UDP 端口')
        if not sdk.UdpSendRequestConnect(0, address, port):
            raise RuntimeError('mHand Studio 连接请求发送失败')
        mocap = MocapData()
        requested, last_frame, last_id, last_emit = clock(), None, None, 0
        while not stop.is_set():
            received = sdk.UdpRecvMocapData(0, address, port, byref(mocap))
            now = clock()
            if received and mocap.isUpdate:
                if last_frame is None:
                    publish(dict(type='status', connected=True, message=f'手套已连接 · {host}:{port}'))
                last_frame = now
                gesture_id = int(mocap.gestureResultR if hand == 'right' else mocap.gestureResultL)
                if gesture_id != last_id or now-last_emit >= .1:
                    publish(dict(type='frame', gesture_id=gesture_id, frame_index=int(mocap.frameIndex),
                                 frequency=int(mocap.frequency)))
                    last_id, last_emit = gesture_id, now
            elif last_frame is None and now-requested >= 3:
                raise TimeoutError('未收到手套数据，请检查 IP、手别和 mHand Studio 广播')
            elif last_frame is not None and now-last_frame >= 2:
                raise TimeoutError('mHand Studio 数据已中断')
            stop.wait(.002)
    finally:
        if opened:
            try:
                sdk.UdpRemove(0, address, port)
            finally:
                sdk.UdpClose(0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--hand', choices=['left', 'right'], required=True)
    parser.add_argument('--sdk', required=True)
    args = parser.parse_args()
    stop = threading.Event()
    def commands():
        try:
            for line in sys.stdin:
                if line.strip() == 'stop':
                    break
        finally:
            stop.set()  # Parent exit/EOF also closes the SDK connection.
    threading.Thread(target=commands, daemon=True).start()
    try:
        receive(load_sdk(args.sdk), args.host, args.port, args.hand, stop)
    except Exception as exc:
        emit(dict(type='status', connected=False, message='手套连接失败：'+str(exc)))
        return 1
    emit(dict(type='status', connected=False, message='手套已断开'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
