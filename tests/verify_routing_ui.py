"""Qt + real HTTP CFG selection; no hardware calls."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtWidgets import QApplication
from linux.service import Recorder, make_server
from windows.app import Window


def run():
    app = QApplication([])
    with tempfile.TemporaryDirectory() as root:
        root = Path(root)
        cfgs = root/'configs'
        cfgs.mkdir()
        cfg = cfgs/'test.cfg'
        cfg.write_text('0(10)0/0;1(11)17.5/0;', 'utf-8')
        rec = Recorder(root/'recordings', 'maxlab', dict(routing_path=str(cfg), prepared_fixed_routing=False),
                       routing_root=cfgs, auto_analyze=False)
        server = make_server(rec, '127.0.0.1', 0, 'routing-ui-test-token')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        w = Window(f'127.0.0.1:{server.server_port}', 'routing-ui-test-token')
        w.show()
        def wait(predicate):
            end = time.monotonic()+8
            while time.monotonic() < end:
                app.processEvents()
                if predicate():
                    return
                time.sleep(.01)
            raise AssertionError('routing UI condition timed out')
        try:
            w.connect_service()
            wait(lambda: w.online and w.worker is None)
            assert w.routing_button.isVisible()
            assert not w.start_button.isEnabled()
            w.routing_button.click()
            wait(lambda: w.routing_dialog is not None and w.worker is None)
            dialog = w.routing_dialog
            assert dialog.combo.count() == 1
            assert not dialog.confirmed.isChecked()
            dialog.accept()
            wait(lambda: w.routing_dialog is None and w.worker is None and rec.config.get('mapping_path'))
            assert not w.start_button.isEnabled()
            w.routing_button.click()
            wait(lambda: w.routing_dialog is not None and w.worker is None)
            dialog = w.routing_dialog
            dialog.confirmed.setChecked(True)
            dialog.accept()
            wait(lambda: w.routing_dialog is None and w.worker is None and w.start_button.isEnabled())
            assert rec.config['prepared_fixed_routing'] is True
            assert len(json.loads(Path(rec.config['mapping_path']).read_text('utf-8'))) == 2
            assert '已确认' in w.routing_button.text()
            # No selection support advertised by old servers; hide the new control.
            w.apply_snapshot(dict(mode='maxlab', job=None, capabilities=[]))
            w.update_buttons()
            assert not w.routing_button.isVisible()
            print('Routing UI checks passed: HTTP listing, unconfirmed gate, explicit confirmation, old-server compatibility')
        finally:
            w.connected = False
            w.timer.stop()
            if w.worker:
                w.worker.wait(6000)
                app.processEvents()
            w.close()
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    run()
