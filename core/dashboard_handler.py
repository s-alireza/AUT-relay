# -*- coding: utf-8 -*-
import json
import base64
import os
import time
import threading
from urllib.parse import unquote, quote
import utils as u
from utils import log, get_logs, clear_logs, html_escape
from web_server import BaseHandler
from vps_installer import automate_vps_setup

class DashboardHandler(BaseHandler):
    def _make_setup_vless_link(self, mgr):
        s = mgr.settings
        # Same logic as ServerManager._make_vless_link but using setup-time defaults
        uuid_val, vps_ip = s.get("vless_uuid", ""), s.get("vps_ip", "0.0.0.0")
        ws_path = s.get("vless_ws_path", "/tunnel")
        if not ws_path.startswith("/"): ws_path = "/" + ws_path
        safe_path = "".join([quote(c) if c != "/" else c for c in ws_path])
        return "vless://{0}@{1}:8080?type=ws&security=none&encryption=none&path={2}&host={1}#AUT-Bridge".format(uuid_val, vps_ip, safe_path)

    def do_GET(self):
        mgr = self.server.mgr
        clean_path = self.path.split("?")[0]
        
        # User Subscription / Dashboard
        if clean_path.startswith("/sub/"):
            parts = [p for p in clean_path.split("/") if p]
            if len(parts) >= 2:
                token = unquote(parts[1])
                user = mgr._find_user_by_token(token)
                if not user: self.send_response(404); self.end_headers(); self.wfile.write(b"not found"); return
                
                if len(parts) >= 3 and parts[2] == "stats":
                    self._send_json({
                        "user_id": user["user_id"], "display_name": user["display_name"],
                        "enabled": user["enabled"], "used_bytes": mgr._user_usage_bytes(user),
                        "quota_bytes": int(user["quota_gb"] * 1024**3),
                        "expiry_date": user["expiry_date"], "subscription_userinfo": mgr._subscription_userinfo(user)
                    })
                    return
                
                ua = (self.headers.get("User-Agent", "") or "").lower()
                client_markers = ["hiddify", "v2rayng", "v2rayn", "shadowrocket", "nekobox", "sing-box", "clash", "loon", "surge", "v2box", "v2ray"]
                is_client = any(m in ua for m in client_markers)
                is_raw_requested = "raw" in self.path.lower()
                
                if is_client or is_raw_requested:
                    if not user.get("enabled", True): self.send_response(403); self.end_headers(); self.wfile.write(b"disabled"); return
                    sub_links = mgr._build_user_subscription_links(user)
                    if not sub_links: self.send_response(204); self.end_headers(); return
                    
                    subscription = "\n".join(sub_links)
                    body = subscription.encode("utf-8") if is_raw_requested else base64.b64encode(subscription.encode("utf-8"))

                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Subscription-Userinfo", mgr._subscription_userinfo(user))
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                
                self._send_html(mgr._build_user_dashboard_html(user, mgr._subscription_url(token)))
                return

        # Main Dashboard Page
        if clean_path == "/":
            try:
                with open(os.path.join(mgr.UI_DIR, "dashboard.html"), "r", encoding="utf-8") as f: self._send_html(f.read())
            except: self._send_html("<h1>dashboard.html not found</h1>")
            return

        if not self.check_auth(): return

        if clean_path == "/api/status":
            s = mgr.get_status()
            s.update({
                "aut_account": mgr.aut_account, "aut_phase": mgr.aut_phase,
                "aut_current_idx": mgr.aut_current_idx, "last_update": mgr.last_aut_update,
                "aut_accounts": [{"username": a["username"]} for a in mgr.aut_accounts],
                "current_usage": mgr.current_usage, "logs": get_logs()
            })
            self._send_json(s)
        elif clean_path == "/api/vps_config":
            s = mgr.settings
            frps_cfg = [
                'bindPort = {0}'.format(s.get("frp_server_port", 443)),
                'quicBindPort = {0}'.format(s.get("frp_server_port", 443)),
                'auth.method = "token"',
                'auth.token = "{0}"'.format(s.get("frp_token", "")),
                'transport.tls.force = true',
                'transport.maxPoolCount = 100'
            ]
            
            toml_val = "\n".join(frps_cfg)
            link_val = self._make_setup_vless_link(mgr)
            self._send_json({"ok": True, "frps_toml": toml_val, "vless_link": link_val})
        elif clean_path == "/api/users":
            ulist = []
            with mgr.user_lock:
                for u in mgr.users:
                    copy = u.copy()
                    copy["subscription_url"] = mgr._subscription_url(u["token"])
                    copy["quota_bytes"] = int(u.get("quota_gb", 0) * 1024**3)
                    ulist.append(copy)
            self._send_json({"ok": True, "users": ulist})
        elif "/api/ping/" in clean_path:
            idx = int(clean_path.split("/")[-1])
            self._send_json({"ok": True, "ms": mgr.ping_server(idx)})
        elif clean_path == "/api/logs":
            self._send_json({"ok": True, "logs": get_logs()})

    def do_POST(self):
        mgr = self.server.mgr
        if self.path == "/api/login":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            token_val = data.get("token", "")
            try:
                decoded = base64.b64decode(token_val).decode("utf-8")
                user_val, pass_val = decoded.split(":", 1)
                if user_val == mgr.settings.get("dashboard_user") and pass_val == mgr.settings.get("dashboard_pass"): self._send_json({"ok": True})
                else: self._send_json({"error": "Invalid"}, 401)
            except: self._send_json({"error": "Invalid"}, 401)
            return

        if not self.check_auth(): return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        data = json.loads(body)
        path = self.path

        if "/api/switch/" in path:
            ok, name = mgr.switch_to(int(path.split("/")[-1]))
            self._send_json({"ok": ok, "name": name})
        elif "/api/aut_switch/" in path:
            mgr.request_aut_switch(int(path.split("/")[-1]))
            self._send_json({"ok": True})
        elif path == "/api/aut_add":
            self._send_json({"ok": mgr.add_aut_account(data["username"], data["password"])})
        elif path == "/api/aut_remove":
            self._send_json({"ok": mgr.remove_aut_account(data["username"])})
        elif path == "/api/add":
            ok, name = mgr.add_server(data["name"], data.get("flag", ""), data["link"])
            self._send_json({"ok": ok, "name": name})
        elif "/api/remove/" in path:
            self._send_json({"ok": mgr.remove_server(int(path.split("/")[-1]))})
        elif path == "/api/subscriptions/add":
            ok, msg = mgr.add_subscription(data["url"], data.get("name", ""))
            self._send_json({"ok": ok, "message": msg})
        elif path == "/api/subscriptions/delete":
            ok, msg = mgr.delete_subscription(data["url"])
            self._send_json({"ok": ok, "message": msg})
        elif path == "/api/subscriptions/update":
            ok, msg = mgr.update_subscription(data["url"])
            self._send_json({"ok": ok, "message": msg})
        elif path == "/api/ping_all":
            self._send_json({"ok": mgr.ping_all_servers()})
        elif path == "/api/logs/clear":
            clear_logs()
            self._send_json({"ok": True})
        elif path == "/api/users":
            ok, res = mgr.create_user(data)
            self._send_json({"ok": ok, "user": res if ok else None, "error": res if not ok else None})
        elif "/api/users/" in path:
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 4:
                uid, action = unquote(parts[2]), parts[3]
                if action == "toggle": ok, res = mgr.toggle_user(uid, data.get("enabled"))
                elif action == "delete": ok, res = mgr.delete_user(uid)
                elif action == "update": ok, res = mgr.update_user(uid, data)
                else: ok, res = False, "Unknown action"
                self._send_json({"ok": ok, "user": res if ok else None, "error": res if not ok else None})
            else: self._send_json({"ok": False, "error": "Invalid path"}, 400)
        elif path == "/api/setup_vps":
            def run_setup():
                log("INFO", "Starting background VPS automation...", component="VPS")
                ok, msg = automate_vps_setup(data["vps_ip"], data.get("ssh_port", 22), data.get("ssh_user", "root"), data["ssh_pass"], data["frps_toml"], mgr.BIN_DIR)
                if ok: 
                    mgr.update_settings({"vps_ip": data["vps_ip"]})
                    log("SUCCESS", "VPS Automation Completed: " + msg, component="VPS")
                else:
                    log("ERROR", "VPS Automation Failed: " + msg, component="VPS")
            threading.Thread(target=run_setup, name="VPSSetup").start()
            self._send_json({"ok": True, "message": "Automation started"})
        elif path == "/api/settings":
            mgr.update_settings(data)
            self._send_json({"ok": True})
        elif path == "/api/system/export":
            with mgr.user_lock:
                bundle = {
                    "settings": mgr.settings,
                    "users": mgr.users,
                    "servers": mgr.servers,
                    "subscriptions": mgr.subscriptions,
                    "aut_accounts": mgr.aut_accounts,
                    "timestamp": u.utc_now_iso()
                }
                self._send_json({"ok": True, "bundle": bundle})
        elif path == "/api/system/import":
            try:
                bundle = data.get("bundle")
                if not bundle: raise Exception("Empty bundle")
                with mgr.user_lock:
                    if "settings" in bundle: mgr.update_settings(bundle["settings"])
                    if "users" in bundle: 
                        mgr.users = bundle["users"]
                        mgr.save_users()
                    if "servers" in bundle:
                        mgr.servers = bundle["servers"]
                        mgr.save_state()
                    if "subscriptions" in bundle:
                        mgr.subscriptions = bundle["subscriptions"]
                        mgr.save_state()
                    if "aut_accounts" in bundle:
                        mgr.aut_accounts = bundle["aut_accounts"]
                        mgr.save_accounts()
                    mgr.debounced_rebuild()
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 400)
        elif path == "/api/system_wipe":
            self._send_json({"ok": True})
            def reset_task():
                log("WARN", "SYSTEM WIPE INITIATED - Deleting configurations...", component="CORE")
                time.sleep(1.5) # Give UI time to receive response and show toast
                
                # Try to log the final message
                log("INFO", "All configurations cleared. Shutting down system...", component="CORE")
                time.sleep(0.5) # Allow log to flush to file/buffer
                
                # Cleanup
                for f in ["settings.json", "accounts.json", "users.json", "servers.json", "config.json", "frpc.toml", "server.log"]:
                    try: 
                        p = os.path.join(mgr.CONFIG_DIR, f)
                        if os.path.exists(p): os.remove(p)
                    except: pass
                
                os._exit(0)
            threading.Thread(target=reset_task).start()
