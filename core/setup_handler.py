# -*- coding: utf-8 -*-
import json
import os
import sys
import time
import threading
import subprocess
from web_server import BaseHandler

# Global setup state (will be attached to server instance)
# setup_done = False

class SetupHandler(BaseHandler):
    def do_GET(self):
        mgr = self.server.mgr
        if self.path == "/":
            if getattr(self.server, 'setup_done', False):
                self._send_html("""
                <html><head><meta http-equiv="refresh" content="3;url=/"><style>
                    body { background: #0f172a; color: white; font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
                    .box { text-align: center; background: rgba(255,255,255,0.05); padding: 40px; border-radius: 20px; border: 1px solid rgba(255,255,255,0.1); }
                    .loader { border: 3px solid rgba(255,255,255,0.1); border-top: 3px solid #3b82f6; border-radius: 50%; width: 30px; height: 30px; animation: spin 1s linear infinite; margin: 0 auto 20px; }
                    @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
                </style></head><body><div class="box"><div class="loader"></div><h2>🚀 Finalizing Setup...</h2><p>Transitioning to the Dashboard. Please wait a moment.</p></div></body></html>
                """)
                return

            html_path = os.path.join(mgr.UI_DIR, "setup.html")
            try:
                with open(html_path, "r", encoding="utf-8") as f: self._send_html(f.read())
            except Exception: self._send_html("<h1>setup.html not found</h1>")
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        mgr = self.server.mgr
        if self.path == "/api/setup":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                data = json.loads(body)
                
                # Use the provided mgr.do_setup (which we'll keep in ServerManager or move)
                vless_link, frp_token, frps_toml = mgr.perform_initial_setup(data)
                self._send_json({"ok": True, "vless_link": vless_link, "frp_token": frp_token, "frps_toml": frps_toml})
                
                def shutdown():
                    self.server.setup_done = True
                    print("\n  Setup completed successfully. Launching bridge manager...")
                    time.sleep(2)
                    self.server.shutdown()
                    
                    # Auto-start account_manager.py
                    try:
                        cmd = [sys.executable, os.path.join(mgr.BASE_DIR, "account_manager.py")]
                        subprocess.Popen(cmd)
                    except Exception as e:
                        print("  Failed to auto-start manager: {0}".format(e))

                threading.Thread(target=shutdown).start()
            except Exception as e:
                import traceback
                traceback.print_exc()
                self._send_json({"ok": False, "error": str(e)}, 500)
        else:
            self.send_response(404); self.end_headers()
