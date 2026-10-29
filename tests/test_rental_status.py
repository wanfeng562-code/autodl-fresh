"""The real HTTP client reads one target and discards unrelated response fields."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading
from types import SimpleNamespace

from playwright.sync_api import sync_playwright

from autodl_fresh.browser import AutoDLConsole
from autodl_fresh.rental_browser import CloneConsole
from autodl_fresh.storage import Settings


def test_real_status_client_uses_an_exact_id_filter_and_only_returns_progress_fields():
    target = 'targetaa-87654321'
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path, data, self.headers.get('Authorization'), self.headers.get('Content-Type')))
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'code':'Success','data':{'list':[{
                'uuid':target,'status':'starting','root_password':'fixture-private-do-not-retain',
                'additional_info':{'copy_data_disk_after_clone_status':'Succeed'}
            }],'result_total':1}}).encode())

        def log_message(self, *args):
            pass

    for port in range(23000, 23100):
        try:
            server = HTTPServer(('127.0.0.1',port), Handler)
            break
        except OSError:
            continue
    else:
        raise RuntimeError('No free fixture port')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as pw:
            client = pw.request.new_context()
            try:
                page = SimpleNamespace(context=SimpleNamespace(request=client), on=lambda *args:None)
                console = AutoDLConsole(page, Settings(console_url=origin+'/console/instance/list'))
                auth = {'origins':[{'origin':origin,'localStorage':[{
                    'name':'token','value':json.dumps({'token':'fixture-auth'})}]}]}
                state = CloneConsole(console, auth_state=auth)._target_state(target)
                assert state == {'clone':'','status':'starting','copy':'Succeed'}
                assert requests == [('/api/v1/instance', {'name':target,'page_index':1,'page_size':1},
                                     'fixture-auth','application/json')]
                assert 'fixture-private' not in repr(state)
            finally:
                client.dispose()
    finally:
        server.shutdown()
        server.server_close()
