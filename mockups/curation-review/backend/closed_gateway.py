"""Optional dedicated Agent 3 gateway; exposes only authenticated preview routes.

Start separately from the private review server. No provider call occurs at
startup or status checks. Requests remain disabled until the owner configures
SPIKE_CLOSED_ENABLE_REQUESTS=1 and explicit provider keys/model allowlists.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from closed_preview import ClosedPreview,PreviewError
from dataset_inputs import DatasetManager,ROOT


def load_cases():
    registry=ROOT/'output/curation_review_local/manager/registry.json'
    manager=DatasetManager.__new__(DatasetManager)
    import threading
    manager.lock=threading.RLock();manager.bundles={};manager.paths={}
    for item in json.loads(registry.read_text()):
        path=Path(item['path']).resolve()
        if not path.is_relative_to(ROOT/'output/curation_review_local'):raise ValueError('Invalid review catalog path')
        manager._load(item['id'],path)
    return manager.all_cases()


class GatewayHandler(BaseHTTPRequestHandler):
    def reply(self,value,status=200):
        data=json.dumps(value,ensure_ascii=False,allow_nan=False).encode() if status!=204 else b''
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        origin=self.headers.get('Origin')
        if origin in self.server.origins:
            self.send_header('Access-Control-Allow-Origin',origin);self.send_header('Vary','Origin')
            self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers','Authorization, Content-Type')
        self.end_headers();self.wfile.write(data)

    def authorized(self):
        origin=self.headers.get('Origin')
        if origin is not None and origin not in self.server.origins:raise PermissionError('Origin is not allowed by this gateway')
        if not secrets.compare_digest(self.headers.get('Authorization',''),'Bearer '+self.server.token):raise PermissionError('A valid gateway access token is required')

    def do_OPTIONS(self):
        if self.headers.get('Origin') not in self.server.origins or urlsplit(self.path).path not in ('/api/closed/status','/api/closed/request'):
            self.reply(dict(error='Not allowed'),403);return
        self.reply({},204)

    def do_GET(self):
        try:
            self.authorized()
            if self.path!='/api/closed/status':self.reply(dict(error='Not found'),404);return
            self.reply(self.server.preview.status())
        except PermissionError as exc:self.reply(dict(error=str(exc)),403)

    def do_POST(self):
        try:
            self.authorized()
            if self.path!='/api/closed/request':self.reply(dict(error='Not found'),404);return
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('Send JSON')
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=8_000_000:raise ValueError('Invalid request size')
            body=json.loads(self.rfile.read(size))
            if not isinstance(body,dict):raise ValueError('Expected a JSON object')
            self.reply(self.server.preview.request(body))
        except PermissionError as exc:self.reply(dict(error=str(exc)),403)
        except ValueError as exc:self.reply(dict(error=str(exc)),400)
        except PreviewError as exc:self.reply(dict(error=str(exc)),502)
        except (OSError,KeyError,TypeError):self.reply(dict(error='Gateway storage or configuration is unavailable'),503)

    def log_message(self,*args):pass


def make_gateway(preview,token,origins,host='127.0.0.1',port=8768):
    if len(token)<32:raise ValueError('Configure a gateway access token of at least 32 characters')
    server=ThreadingHTTPServer((host,port),GatewayHandler)
    server.preview=preview;server.token=token;server.origins=set(origins)
    return server


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8768)
    args=parser.parse_args()
    token=os.environ.get('SPIKE_CLOSED_GATEWAY_TOKEN','')
    if len(token)<32:parser.error('Set SPIKE_CLOSED_GATEWAY_TOKEN outside the repository before starting the gateway')
    origins=os.environ.get('SPIKE_CLOSED_ALLOWED_ORIGINS','https://spikeagent-review-20261023.web.app,https://spikeagent-review-20261023.firebaseapp.com').split(',')
    preview=ClosedPreview(load_cases(),ROOT/'output/curation_review_local/closed_preview/requests.sqlite3')
    server=make_gateway(preview,token,origins,args.host,args.port)
    print('Agent 3 gateway ready; live requests enabled:',preview.config['enabled'],flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
