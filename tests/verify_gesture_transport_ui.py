"""Qt HTTP round-trip with synthetic frames; no glove or Maxwell hardware."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication
from windows.app import Window, configure_fonts
from linux.service import Recorder, make_server
from shared.gestures import GESTURES

def run():
    app=QApplication([]);configure_fonts(app)
    with tempfile.TemporaryDirectory() as directory:
        recorder=Recorder(directory,auto_analyze=False)
        server=make_server(recorder,'127.0.0.1',0,'test-token-0123456789')
        thread=threading.Thread(target=server.serve_forever);thread.start()
        window=Window(f'127.0.0.1:{server.server_port}','test-token-0123456789')
        page=window.game_panel
        def wait(predicate):
            until=time.monotonic()+5
            while time.monotonic()<until:
                app.processEvents()
                if predicate(): return
                time.sleep(.01)
            raise AssertionError('UI transport timeout: '+page.delivery.text())
        def count():
            with closing(sqlite3.connect(recorder.gesture_log.path)) as db:
                return db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        try:
            window.connect_service();wait(lambda:window.online)
            assert page.send_checkbox.isEnabled() and not page.send_checkbox.isChecked()
            page.glove.connected=True;page.send_checkbox.setChecked(True)
            for i,(gid,name) in enumerate(GESTURES.items()):
                frame=dict(gesture_id=gid,frame_index=i,frequency=60)
                page.glove.frame(frame,now=i*2)
                page.glove.frame(frame,now=i*2+.7)
                wait(lambda:count()==i+1 and 'Linux 已接收：'+name in page.delivery.text())
                page.glove.frame(frame,now=i*2+1)
                app.processEvents();assert count()==i+1
            page.glove.connected=False;page.connection_changed(False,'test disconnected')
            assert not page.sender.base
            window.connect_service()
            assert not page.send_checkbox.isChecked() and not page.send_checkbox.isEnabled()
            page.glove.frame(dict(gesture_id=16,frame_index=9,frequency=60),now=20)
            page.glove.frame(dict(gesture_id=16,frame_index=10,frequency=60),now=21)
            assert count()==5
            page.set_service('http://127.0.0.1:1','test',False)
            assert not page.send_checkbox.isEnabled()
            print('PASS: five stable gestures acknowledged; held gestures deduplicated; disconnect/old server disables transport; no stimulation')
        finally:
            window.close();app.processEvents();server.shutdown();thread.join();server.server_close()
if __name__=='__main__':run()
