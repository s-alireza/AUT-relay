# -*- coding: utf-8 -*-
import json
import base64
import os
import time
import threading
from utils import log, read_json_file, html_escape
try:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    try:
        from http.server import ThreadingHTTPServer
    except ImportError:
        import socketserver
        class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer): pass
except ImportError:
    from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
    import SocketServer
    class ThreadingHTTPServer(SocketServer.ThreadingMixIn, HTTPServer): pass

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
    try:
        # On Windows, 127.0.0.1 is sometimes more reliable than 0.0.0.0 for internal tools,
        # but the dashboard needs to be accessible via FRP, which points to 127.0.0.1.
        # However, many users report 10013 is solved by binding to 127.0.0.1 if 0.0.0.0 is restricted.
        server = ThreadingHTTPServer(("127.0.0.1", port), handler_class)
        server.mgr = mgr
        log("OK", "{0} started on 127.0.0.1:{1}".format(name, port))
        
        t = threading.Thread(target=server.serve_forever, name=name)
        t.daemon = True
        t.start()
        return server
    except Exception as e:
        log("ERROR", "Failed to start {0} on port {1}: {2}".format(name, port, e))
        # If it's a critical port like the dashboard, we might want to try 127.0.0.1 
        # specifically if 0.0.0.0 failed, but here we already switched to 127.0.0.1
        # as a safer default for local relaying.
        return None
