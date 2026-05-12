# -*- coding: utf-8 -*-
import json
import base64
import os
import time
import threading
from utils import log, read_json_file, html_escape
try:
    from http.server import HTTPServer, BaseHTTPRequestHandler
except ImportError:
    from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler

class BaseHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass
    
    def _send_json(self, data, code=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def check_auth(self):
        # Access settings through server.mgr if available
        mgr = getattr(self.server, 'mgr', None)
        if not mgr: return True # Fallback for setup phase
        
        s = mgr.settings
        user, pwd = s.get("dashboard_user"), s.get("dashboard_pass")
        if not user: return False
        expected = base64.b64encode("{0}:{1}".format(user, pwd).encode("utf-8")).decode("utf-8")
        auth = self.headers.get("X-Auth-Token") or (self.headers.get("Authorization") or "").replace("Basic ", "")
        if auth == expected: return True
        self._send_json({"error": "Unauthorized"}, 401); return False

def start_server(handler_class, mgr, port, name="Web Server"):
    server = HTTPServer(("0.0.0.0", port), handler_class)
    server.mgr = mgr
    log("OK", "{0} started on port {1}".format(name, port))
    
    t = threading.Thread(target=server.serve_forever)
    t.daemon = True
    t.start()
    return server
