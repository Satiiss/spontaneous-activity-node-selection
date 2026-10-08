"""Asynchronous LAN gesture transport. No queuing or replay after disconnect."""
import json
import uuid
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkRequest, QNetworkReply
from shared.gestures import GESTURE_PATH, GESTURES


class GestureSender(QObject):
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.network = QNetworkAccessManager(self)
        self.network.setProxy(QNetworkProxy(QNetworkProxy.NoProxy))
        self.base = self.token = ''
        self.reply = None
        self.generation = 0

    def configure(self, base, token):
        if (base, token) == (self.base, self.token):
            return
        self.generation += 1
        self.base, self.token = base, token
        if self.reply:
            self.reply.abort()

    def send(self, frame):
        if not self.base:
            return
        name = GESTURES[frame['gesture_id']]
        if self.reply is not None:
            self.status.emit(name+'未发送：上一条手势仍在等待 Linux 回执，请重新做手势')
            return
        payload = dict(event_id=uuid.uuid4().hex, gesture_id=frame['gesture_id'],
                       frame_index=frame['frame_index'], hand=frame['hand'])
        request = QNetworkRequest(QUrl(self.base+GESTURE_PATH))
        request.setRawHeader(b'Authorization', ('Bearer '+self.token).encode())
        request.setHeader(QNetworkRequest.ContentTypeHeader, 'application/json')
        request.setTransferTimeout(3000)
        generation = self.generation
        reply = self.network.post(request, json.dumps(payload).encode())
        self.reply = reply
        self.status.emit('正在发送：'+name)
        reply.finished.connect(lambda: self.finished(reply, generation, payload))

    def finished(self, reply, generation, payload):
        if self.reply is reply:
            self.reply = None
        if generation == self.generation:
            try:
                if reply.error() != QNetworkReply.NoError:
                    raise ValueError(reply.errorString())
                result = json.loads(bytes(reply.readAll()))
                if result.get('event_id') != payload['event_id'] or result.get('gesture_id') != payload['gesture_id'] or result.get('received') is not True or result.get('stimulated') is not False:
                    raise ValueError('Linux 回执格式不匹配')
                self.status.emit('Linux 已接收：'+result['gesture']+' · '+result['received_at']+' · MEA 刺激未接入')
            except Exception as exc:
                self.status.emit('手势传输未确认：'+str(exc)+'；不自动重发，请重新做手势')
        reply.deleteLater()
