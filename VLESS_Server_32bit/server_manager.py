# -*- coding: utf-8 -*-
"""
AUT Proxy Bridge - Server Manager
Remote VPN server switching via a mobile web dashboard.
Python 3.4+ compatible. Runs on Windows 7 32-bit.
"""
import json
import os
import sys
import time
import base64
import re
import subprocess
import threading
import socket
from datetime import datetime
try:
    from http.server import HTTPServer, BaseHTTPRequestHandler
except ImportError:
    from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
try:
    from urllib.parse import urlparse, parse_qs, unquote
except ImportError:
    from urlparse import urlparse, parse_qs, unquote

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
UI_DIR = os.path.join(BASE_DIR, "ui")
BIN_DIR = os.path.join(BASE_DIR, "bin")

def load_settings():
    """Load infrastructure settings from settings.json."""
    path = os.path.join(CONFIG_DIR, "settings.json")
    try:
        if not os.path.exists(path):
            log("WARN", "settings.json missing!")
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def log(level, msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print("[{0}] {1}  {2}".format(ts, level.ljust(5), msg))

# ─── Config Link Parser ────────────────────────────────────────────────────

def parse_config_link(link):
    """Parse ss://, vless://, or vmess:// links into Xray outbound dicts."""
    link = link.strip()
    if link.startswith("ss://"):
        return _parse_ss(link)
    elif link.startswith("vless://"):
        return _parse_vless(link)
    elif link.startswith("vmess://"):
        return _parse_vmess(link)
    else:
        return None

def _parse_ss(link):
    """Parse ss://BASE64(method:password)@host:port#name"""
    try:
        link = link.split("#")[0]
        body = link[5:]  # remove ss://
        if "@" in body:
            user_part, server_part = body.rsplit("@", 1)
        else:
            decoded = base64.b64decode(body + "==").decode("utf-8")
            if "@" in decoded:
                user_part, server_part = decoded.rsplit("@", 1)
            else:
                return None
        # Decode user_part if base64
        try:
            user_part = base64.b64decode(user_part + "==").decode("utf-8")
        except Exception:
            pass
        method, password = user_part.split(":", 1)
        host, port = server_part.split(":", 1)
        port = int(port)
        return {
            "protocol": "shadowsocks",
            "settings": {
                "servers": [{
                    "address": host,
                    "port": port,
                    "method": method,
                    "password": password
                }]
            }
        }
    except Exception as e:
        log("ERROR", "Failed to parse SS link: {0}".format(e))
        return None

def _parse_vless(link):
    """Parse vless://uuid@host:port?params#name"""
    try:
        link = link.split("#")[0]
        body = link[8:]  # remove vless://
        uuid, rest = body.split("@", 1)
        if "?" in rest:
            server_part, query = rest.split("?", 1)
        else:
            server_part, query = rest, ""
        host, port = server_part.rsplit(":", 1)
        port = int(port)
        params = parse_qs(query)
        get = lambda k, d="": params.get(k, [d])[0]

        outbound = {
            "protocol": "vless",
            "settings": {
                "vnext": [{
                    "address": host,
                    "port": port,
                    "users": [{"id": uuid, "encryption": "none"}]
                }]
            },
            "streamSettings": {}
        }
        ss = outbound["streamSettings"]
        net = get("type", "tcp")
        ss["network"] = net
        security = get("security", "none")
        if security == "tls":
            ss["security"] = "tls"
            sni = get("sni", host)
            ss["tlsSettings"] = {"serverName": sni, "allowInsecure": True}
        if net == "ws":
            path = get("path", "/")
            ss["wsSettings"] = {"path": unquote(path)}
            h = get("host", "")
            if h:
                ss["wsSettings"]["headers"] = {"Host": h}
        elif net == "grpc":
            ss["grpcSettings"] = {"serviceName": get("serviceName", "")}
        return outbound
    except Exception as e:
        log("ERROR", "Failed to parse VLESS link: {0}".format(e))
        return None

def _parse_vmess(link):
    """Parse vmess://BASE64(json)"""
    try:
        body = link[8:].split("#")[0]
        padding = 4 - len(body) % 4
        if padding != 4:
            body += "=" * padding
        data = json.loads(base64.b64decode(body).decode("utf-8"))
        outbound = {
            "protocol": "vmess",
            "settings": {
                "vnext": [{
                    "address": data.get("add", ""),
                    "port": int(data.get("port", 443)),
                    "users": [{
                        "id": data.get("id", ""),
                        "alterId": int(data.get("aid", 0)),
                        "security": data.get("scy", "auto")
                    }]
                }]
            },
            "streamSettings": {}
        }
        ss = outbound["streamSettings"]
        net = data.get("net", "tcp")
        ss["network"] = net
        tls = data.get("tls", "")
        if tls == "tls":
            ss["security"] = "tls"
            ss["tlsSettings"] = {"serverName": data.get("sni", data.get("add", "")), "allowInsecure": True}
        if net == "ws":
            ss["wsSettings"] = {"path": data.get("path", "/"), "headers": {"Host": data.get("host", "")}}
        return outbound
    except Exception as e:
        log("ERROR", "Failed to parse VMess link: {0}".format(e))
        return None

# ─── Shared State (managed by ServerManager instance) ────────────────────────

_mgr = None  # Will be set by start_web_server

# ─── Server Manager ─────────────────────────────────────────────────────────

class ServerManager:
    """Manages VPN exit server configurations and switching."""

    def __init__(self):
        self.servers = []
        self.active_index = 0
        self.settings = load_settings()
        self.port = self.settings.get("management_port", 3080)
        
        # AUT Account State
        self.aut_account = "None"
        self.aut_phase = "IDLE"
        self.aut_accounts = []
        self.aut_usage = {}
        self.aut_current_idx = 0
        self.aut_pending_idx = -1
        self.current_usage = {}
        self.force_switch = -1
        self.wake_event = threading.Event()
        self.last_aut_update = datetime.now().strftime("%H:%M:%S")
        self.tunnel_status = "Unknown"
        self.last_tunnel_error = ""
        self.aut_target_account = ""

        self._read_inbound()
        self.load_servers()
        # Ensure config is written and Xray is started with the new inbounds immediately
        self.switch_to(self.active_index)

    def set_aut_status(self, account, phase, accounts=None, usage=None, current_idx=0, current_usage=None):
        self.aut_account = account
        self.aut_phase = phase
        if accounts is not None:
            self.aut_accounts = accounts
        if usage is not None:
            self.aut_usage = usage
        self.aut_current_idx = current_idx
        
        if self.aut_pending_idx >= 0 and current_idx == self.aut_pending_idx:
            if not self.aut_target_account or self.aut_target_account == account:
                self.aut_pending_idx = -1
                self.aut_target_account = ""
        
        if current_usage is not None:
            self.current_usage = current_usage
        self.last_aut_update = datetime.now().strftime("%H:%M:%S")

    def set_tunnel_status(self, status, error=""):
        self.tunnel_status = status
        self.last_tunnel_error = error

    def request_aut_switch(self, index):
        self.force_switch = index
        self.aut_pending_idx = index
        if index >= 0 and index < len(self.aut_accounts):
            self.aut_target_account = self.aut_accounts[index].get("username", "")
        else:
            self.aut_target_account = ""
        log("INFO", "AUT switch requested: idx={0} target={1}".format(index, self.aut_target_account or "?"))
        self.wake_event.set()  # wake the main loop immediately

    def consume_force_switch(self):
        idx = self.force_switch
        self.force_switch = -1
        return idx

    def wait_for_interval(self, seconds):
        """Sleep for `seconds` but wake up instantly if a switch is requested."""
        self.wake_event.wait(timeout=seconds)
        self.wake_event.clear()

    def add_aut_account(self, username, password):
        """Add a new account to accounts.json."""
        path = os.path.join(CONFIG_DIR, "accounts.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["accounts"].append({"username": username, "password": password})
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self.aut_accounts.append({"username": username, "password": password})
            return True
        except Exception as e:
            log("ERROR", "Failed to add account: {0}".format(e))
            return False

    def _read_inbound(self):
        path = os.path.join(CONFIG_DIR, "config.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.inbound = cfg.get("inbounds", [{}])[0]
        except Exception:
            s = self.settings
            self.inbound = {
                "port": s.get("vless_port", 4433), "listen": "0.0.0.0", "protocol": "vless",
                "settings": {"clients": [{"id": s.get("vless_uuid", ""), "flow": ""}], "decryption": "none"},
                "streamSettings": {"network": "ws", "wsSettings": {"path": s.get("vless_ws_path", "/tunnel")}}
            }

    def load_servers(self):
        path = os.path.join(CONFIG_DIR, "servers.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.servers = data.get("servers", [])
            self.active_index = data.get("active_index", 0)
            self.port = data.get("management_port", 3080)
        except Exception:
            self.servers = [{"name": "Freedom", "flag": "", "outbound": {"protocol": "freedom"}}]
            self.active_index = 0

    def save_state(self):
        path = os.path.join(CONFIG_DIR, "servers.json")
        data = {"active_index": self.active_index, "management_port": self.port, "servers": self.servers}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def switch_to(self, index):
        if index < 0 or index >= len(self.servers):
            return False, "Invalid index"
        self.active_index = index
        server = self.servers[index]
        s = self.settings
        
        # Build complex config with routing
        proxy_outbound = server["outbound"].copy()
        proxy_outbound["tag"] = "proxy"
        
        direct_outbound = {"tag": "direct", "protocol": "freedom", "settings": {}}
        
        vps_ip = s.get("vps_ip", "127.0.0.1")
        routing = {
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                {
                    "type": "field",
                    "outboundTag": "direct",
                    "ip": [vps_ip]
                },
                {
                    "type": "field",
                    "outboundTag": "direct",
                    "domain": ["regexp:\\.ir$", "youtube.com", "googlevideo.com", "ytimg.com", "google.com"],
                    "ip": ["geoip:ir"]
                },
                {
                    "type": "field",
                    "outboundTag": "proxy",
                    "port": "0-65535"
                }
            ]
        }
        
        http_inbound = {
            "tag": "http-inbound",
            "port": s.get("http_proxy_port", 1080),
            "listen": "127.0.0.1",
            "protocol": "http",
            "settings": {"auth": "noauth", "udp": True}
        }

        socks_inbound = {
            "tag": "socks-inbound",
            "port": s.get("socks_proxy_port", 1082),
            "listen": "127.0.0.1",
            "protocol": "socks",
            "settings": {"auth": "noauth", "udp": True}
        }
        
        xray_cfg = {
            "inbounds": [self.inbound, http_inbound, socks_inbound],
            "outbounds": [proxy_outbound, direct_outbound],
            "routing": routing
        }
        
        with open(os.path.join(CONFIG_DIR, "config.json"), "w", encoding="utf-8") as f:
            json.dump(xray_cfg, f, indent=2)
        self.save_state()
        self._restart_xray()
        log("OK", "VPN Switched: {0} {1} (Routing: IR/YT direct)".format(server.get("flag", ""), server["name"]))
        return True, server["name"]

    def add_server(self, name, flag, link):
        outbound = parse_config_link(link)
        if outbound is None:
            return False, "Could not parse config link"
        proto = outbound.get("protocol", "unknown")
        entry = {"name": name, "flag": flag, "protocol": proto, "outbound": outbound, "link": link}
        self.servers.append(entry)
        self.save_state()
        return True, name

    def remove_server(self, index):
        if index < 0 or index >= len(self.servers):
            return False
        if index == self.active_index:
            return False
        self.servers.pop(index)
        if self.active_index > index:
            self.active_index -= 1
        self.save_state()
        return True

    def get_status(self):
        slist = []
        for s in self.servers:
            slist.append({"name": s["name"], "flag": s.get("flag", ""), "protocol": s.get("protocol", s["outbound"].get("protocol", ""))})
        return {
            "active_index": self.active_index, 
            "servers": slist,
            "tunnel_status": self.tunnel_status,
            "tunnel_error": self.last_tunnel_error
        }

    def _restart_xray(self):
        try:
            os.system('taskkill /f /im xray.exe 2>nul')
        except Exception:
            pass
        time.sleep(1)
        try:
            xray = os.path.join(BIN_DIR, "xray.exe")
            with open(os.path.join(CONFIG_DIR, "xray.log"), "w") as log_file:
                subprocess.Popen([xray], cwd=CONFIG_DIR, stdout=log_file, stderr=log_file)
            log("OK", "Xray restarted (Routing active)")
        except Exception as e:
            log("ERROR", "Failed to restart Xray: {0}".format(e))

    def ping_server(self, index):
        """Check the TCP latency of a server."""
        if index < 0 or index >= len(self.servers):
            return -1
        server = self.servers[index]
        if server["outbound"].get("protocol") == "freedom":
            return 0
            
        # Extract host and port
        host, port = None, None
        try:
            outbound = server["outbound"]
            if outbound["protocol"] == "shadowsocks":
                s = outbound["settings"]["servers"][0]
                host, port = s["address"], s["port"]
            elif outbound["protocol"] in ["vless", "vmess"]:
                s = outbound["settings"]["vnext"][0]
                host, port = s["address"], s["port"]
            
            if not host or not port:
                return -1
                
            start = time.time()
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect((host, int(port)))
            s.close()
            return int((time.time() - start) * 1000)
        except Exception:
            return -1

# ─── HTTP Handler ────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # Suppress default logging

    def _send_html(self, html):
        try:
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass  # Likely connection aborted

    def _send_json(self, data, code=200):
        try:
            body = json.dumps(data).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, proxy-revalidate")
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass  # Likely connection aborted

    def check_auth(self):
        # Always reload settings to ensure we are using the latest credentials
        current_settings = load_settings()
        user = current_settings.get("dashboard_user")
        pwd = current_settings.get("dashboard_pass")
        
        if not user or not pwd:
            log("ERROR", "AUTH CONFIG MISSING! Access denied.")
            self._send_json({"error": "Server not configured"}, 401)
            return False
            
        expected = base64.b64encode("{0}:{1}".format(user, pwd).encode("utf-8")).decode("utf-8")
        auth_header = self.headers.get("Authorization") or self.headers.get("X-Auth-Token")
        
        if auth_header:
            if auth_header.startswith("Basic "):
                auth_header = auth_header[6:]
            
            # Simple direct comparison of the base64 tokens
            if auth_header == expected:
                return True
            else:
                log("WARN", "Auth failed: Wrong credentials from {0}".format(self.client_address[0]))
        
        self._send_json({"error": "Unauthorized"}, 401)
        return False

    def do_GET(self):
        if self.path == "/":
            html_path = os.path.join(UI_DIR, "dashboard.html")
            try:
                with open(html_path, "r", encoding="utf-8") as f:
                    self._send_html(f.read())
            except Exception:
                self._send_html("<h1>dashboard.html not found</h1>")
            return

        if not self.check_auth():
            return

        if self.path.startswith("/api/status"):
            status = _mgr.get_status()
            status["aut_account"] = _mgr.aut_account
            status["aut_phase"] = _mgr.aut_phase
            status["aut_current_idx"] = _mgr.aut_current_idx
            status["last_update"] = _mgr.last_aut_update
            # Build account list
            accts = []
            for i, a in enumerate(_mgr.aut_accounts):
                accts.append({"username": a.get("username", "")})
            status["aut_accounts"] = accts
            # Usage
            cu = _mgr.current_usage
            status["current_usage"] = {
                "daily_used": cu.get("daily_used") or 0,
                "weekly_used": cu.get("weekly_used") or 0,
                "monthly_used": cu.get("monthly_used") or 0,
                "free_used": cu.get("free_used") or 0,
            }
            self._send_json(status)
        elif self.path.startswith("/api/vps_config"):
            # Provide the configuration block for the VPS (INI for 32-bit FRP)
            s = _mgr.settings
            vless_link = "vless://{0}@{1}:{2}?type=ws&security=none&path={3}#AUT-Relay".format(
                s.get("vless_uuid", ""), s.get("vps_ip", ""), s.get("remote_xray_port", 8080), 
                unquote(s.get("vless_ws_path", "/tunnel"))
            )
            
            # Generate the FRPS INI block
            frps_cfg = [
                '[common]',
                'bind_port = {0}'.format(s.get("frp_server_port", 443)),
                'token = {0}'.format(s.get("frp_token", "")),
                'tls_enable = true',
                '',
                '# Xray VLESS Inbound',
                '# xray --config config.json'
            ]
            
            self._send_json({
                "ok": True,
                "vless_link": vless_link,
                "frps_toml": "\n".join(frps_cfg) # Named toml in API for consistency but content is INI
            })
        elif self.path.startswith("/api/ping/"):
            try:
                idx = int(self.path.split("/")[-1])
                ms = _mgr.ping_server(idx)
                self._send_json({"ok": True, "ms": ms})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        # Exclude login from mandatory check
        if self.path != "/api/login" and not self.check_auth():
            return
        if self.path.startswith("/api/switch/"):
            try:
                idx = int(self.path.split("/")[-1])
                ok, name = _mgr.switch_to(idx)
                self._send_json({"ok": ok, "name": name})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)
        elif self.path.startswith("/api/aut_switch/"):
            try:
                idx = int(self.path.split("/")[-1])
                if idx < 0 or idx >= len(_mgr.aut_accounts):
                    self._send_json({"ok": False, "error": "Invalid index"})
                else:
                    _mgr.request_aut_switch(idx)
                    self._send_json({"ok": True, "name": _mgr.aut_accounts[idx].get("username", "")})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)
        elif self.path == "/api/login":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                data = json.loads(body)
                token = data.get("token")
                
                # Decode and split to compare directly
                decoded = base64.b64decode(token).decode("utf-8")
                if ":" not in decoded:
                    self._send_json({"error": "Malformed token"}, 401)
                    return
                
                req_user, req_pwd = decoded.split(":", 1)
                
                current_settings = load_settings()
                user = current_settings.get("dashboard_user")
                pwd = current_settings.get("dashboard_pass")
                
                if req_user == user and req_pwd == pwd:
                    log("INFO", "Login successful for user: {0}".format(req_user))
                    self._send_json({"ok": True})
                else:
                    log("WARN", "Login failed for user: {0}".format(req_user))
                    self._send_json({"error": "Invalid username or password"}, 401)
            except Exception as e:
                log("ERROR", "Login error: {0}".format(e))
                self._send_json({"error": str(e)}, 500)
        elif self.path == "/api/add":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                data = json.loads(body)
                ok, name = _mgr.add_server(data["name"], data.get("flag", ""), data["link"])
                self._send_json({"ok": ok, "name": name})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)
        elif self.path.startswith("/api/remove/"):
            try:
                idx = int(self.path.split("/")[-1])
                ok = _mgr.remove_server(idx)
                self._send_json({"ok": ok})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)
        elif self.path == "/api/aut_add":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                data = json.loads(body)
                ok = _mgr.add_aut_account(data["username"], data["password"])
                self._send_json({"ok": ok})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)
        elif self.path == "/api/reset":
            try:
                self._send_json({"ok": True})
                
                # Perform reset in a background thread so the response sends
                def do_reset_and_exit():
                    time.sleep(1)
                    files_to_delete = [
                        os.path.join(CONFIG_DIR, "settings.json"),
                        os.path.join(CONFIG_DIR, "accounts.json"),
                        os.path.join(CONFIG_DIR, "accounts_usage.json"),
                        os.path.join(CONFIG_DIR, "servers.json"),
                        os.path.join(CONFIG_DIR, "config.json"),
                        os.path.join(CONFIG_DIR, "frpc.toml"),
                        os.path.join(CONFIG_DIR, "frpc.ini"),
                        os.path.join(CONFIG_DIR, "xray.log"),
                        os.path.join(BASE_DIR, "out.log"),
                    ]
                    for fpath in files_to_delete:
                        try:
                            if os.path.exists(fpath):
                                os.remove(fpath)
                        except Exception:
                            pass
                    # Stop Xray
                    try:
                        os.system('taskkill /f /im xray.exe 2>nul')
                    except Exception:
                        pass
                    # Hard exit the entire program (Account Manager & Dashboard)
                    os._exit(0)
                threading.Thread(target=do_reset_and_exit).start()
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)
        else:
            self.send_response(404)
            self.end_headers()

def start_web_server(mgr, port=3080):
    global _mgr
    _mgr = mgr
    try:
        server = HTTPServer(("0.0.0.0", port), Handler)
        log("OK", "Management dashboard on http://127.0.0.1:{0}".format(port))
        vps_ip = mgr.settings.get("vps_ip")
        if vps_ip:
            log("OK", "Remote dashboard on http://{0}:8880".format(vps_ip))
        server.serve_forever()
    except Exception as e:
        log("ERROR", "Could not start web server on port {0}: {1}".format(port, e))
        log("ERROR", "Make sure no other instance is running.")
        raise e

if __name__ == "__main__":
    mgr = ServerManager()
    start_web_server(mgr, mgr.port)
