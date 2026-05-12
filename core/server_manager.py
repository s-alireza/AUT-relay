# -*- coding: utf-8 -*-
import os
import time
import re
import socket
import threading
import json
import subprocess
import base64
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, unquote

import utils as u
from utils import html_escape
from config_parser import parse_config_link

class ServerManager:
    def __init__(self, process_manager=None):
        self.BASE_DIR = u.BASE_DIR
        self.CONFIG_DIR = u.CONFIG_DIR
        self.UI_DIR = u.UI_DIR
        self.BIN_DIR = u.BIN_DIR
        self.USERS_PATH = os.path.join(self.CONFIG_DIR, "users.json")
        
        self.process_manager = process_manager
        self.settings = self.load_settings()
        self.port = self.settings.get("management_port", 3080)
        self.vps_ip = self.settings.get("vps_ip", "0.0.0.0")
        self.dashboard_domain = self.settings.get("dashboard_domain", "")
        
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
        
        self.user_lock = threading.RLock()
        self.users = []
        self._last_xray_stats = {} 
        self.subscriptions = []
        self.last_pings = {}
        self.ping_timeout_ms = self.settings.get("ping_timeout_ms", 2000)
        u.set_max_log_size(self.settings.get("max_log_size", 100))
        
        self._rebuild_timer = None
        self._geo_cache = {}
        self._geo_lock = threading.Lock()
        self._geo_worker_running = False
        self._initial_ping_done = False
        
        # Thread Pool for pings and geo-lookups
        self.executor = ThreadPoolExecutor(max_workers=20)

        self._read_inbound()
        self.load_servers()
        self.users = self.load_users()
        self._update_xray_config_users()
        # Force an immediate build on first startup to avoid race with Xray process launch
        self.active_index = self.active_index # Ensure it's set
        self.rebuild_xray_config() 
        self._update_local_frp_config(restart=False)

    def start_background_tasks(self):
        """Start maintenance threads only when explicitly called."""
        u.log("INFO", "Starting background maintenance tasks...", component="CORE")
        
        def run_loop(target, name):
            t = threading.Thread(target=target, name=name)
            t.daemon = True
            t.start()
            return t

        run_loop(self._user_maintenance_loop, "UserMaint")
        run_loop(self._subscription_update_loop, "SubUpdate")
        run_loop(self._auto_ping_loop, "AutoPing")
        self.resolve_all_geos()

    def load_settings(self):
        path = os.path.join(self.CONFIG_DIR, "settings.json")
        return u.read_json_file(path, {})

    def update_settings(self, new_data):
        with self.user_lock:
            for k, v in new_data.items():
                self.settings[k] = v
            u.write_json_file(os.path.join(self.CONFIG_DIR, "settings.json"), self.settings)
            self.vps_ip = self.settings.get("vps_ip", self.vps_ip)
            self.dashboard_domain = self.settings.get("dashboard_domain", self.dashboard_domain)
            self.ping_timeout_ms = self.settings.get("ping_timeout_ms", self.ping_timeout_ms)
            u.set_max_log_size(self.settings.get("max_log_size", 100))
            self._update_local_frp_config(restart=True)
        return True

    def _update_local_frp_config(self, restart=False):
        s = self.settings
        vps_ip = s.get("vps_ip", "0.0.0.0")
        domain = self.dashboard_domain

        frpc_cfg = [
            '[common]',
            'server_addr = {0}'.format(vps_ip),
            'server_port = {0}'.format(s.get("frp_server_port", 443)),
            'protocol = {0}'.format(s.get("frp_protocol", "tcp")),
            'token = {0}'.format(s.get("frp_token", "")),
            'tls_enable = true',
            'heartbeat_interval = 10',
            'heartbeat_timeout = 30',
            'login_fail_exit = false',
            '',
            '[xray_ws]',
            'type = tcp',
            'local_ip = 127.0.0.1',
            'local_port = {0}'.format(s.get("vless_port", 4433)),
            'remote_port = {0}'.format(s.get("remote_xray_port", 8080)),
            '',
            '[dashboard_tcp]',
            'type = tcp',
            'local_ip = 127.0.0.1',
            'local_port = {0}'.format(s.get("management_port", 3080)),
            'remote_port = {0}'.format(s.get("remote_dashboard_port", 8880)),
            ''
        ]

        frpc_cfg.extend([
            '',
            '[http_tunnel]',
            'type = tcp',
            'local_ip = 127.0.0.1',
            'local_port = {0}'.format(s.get("http_proxy_port", 1080)),
            'remote_port = {0}'.format(s.get("remote_http_port", 1081)),
            '',
            '[socks_tunnel]',
            'type = tcp',
            'local_ip = 127.0.0.1',
            'local_port = {0}'.format(s.get("socks_proxy_port", 1082)),
            'remote_port = {0}'.format(s.get("remote_socks_port", 1083)),
            ''
        ])

        path = os.path.join(self.CONFIG_DIR, "frpc.ini")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(frpc_cfg))

        if restart and self.process_manager:
            self.process_manager.trigger_restart("FRPC")

    def set_aut_status(self, account, phase, accounts=None, usage=None, current_idx=0, current_usage=None):
        self.aut_account = account
        self.aut_phase = phase
        if accounts is not None: self.aut_accounts = accounts
        if usage is not None: self.aut_usage = usage
        self.aut_current_idx = current_idx
        if self.aut_pending_idx >= 0 and current_idx == self.aut_pending_idx:
            if not self.aut_target_account or self.aut_target_account == account:
                self.aut_pending_idx = -1
                self.aut_target_account = ""
        if current_usage is not None: self.current_usage = current_usage
        self.last_aut_update = datetime.now().strftime("%H:%M:%S")

    def set_tunnel_status(self, status, error=""):
        old_status = self.tunnel_status
        self.tunnel_status = status
        self.last_tunnel_error = error

        if old_status != "Connected" and status == "Connected" and not self._initial_ping_done:
            u.log("OK", "Tunnel established. Running initial server verification...", component="CORE")
            self._initial_ping_done = True
            self.ping_all_servers()

    def load_users(self):
        data = u.read_json_file(self.USERS_PATH, {"users": []})
        users = [self._normalize_user(raw) for raw in data.get("users", [])]
        
        if not any(u["user_id"].lower() == "admin" for u in users):
            all_indices = list(range(len(self.servers)))
            uuids = {str(i): u.generate_uuid() for i in all_indices}
            admin_user = self._normalize_user({
                "user_id": "admin", "display_name": "System Admin",
                "token": u.generate_token(32), "quota_gb": 0, "enabled": True,
                "servers": all_indices, "uuids": uuids
            })
            users.insert(0, admin_user)
            self.users = users
            self.save_users()
        return users

    def save_users(self):
        with self.user_lock:
            u.write_json_file(self.USERS_PATH, {"users": self.users})

    def _normalize_user(self, raw):
        token = str(raw.get("token", "")).strip() or u.generate_token(32)
        user = {
            "user_id": str(raw.get("user_id", "")).strip(),
            "display_name": str(raw.get("display_name", "")).strip() or str(raw.get("user_id", "")).strip(),
            "token": token,
            "enabled": bool(raw.get("enabled", True)),
            "allow_direct": bool(raw.get("allow_direct", True)),
            "allow_cdn": bool(raw.get("allow_cdn", True)),
            "quota_gb": float(raw.get("quota_gb", 0) or 0),
            "usage_bytes": u.to_int(raw.get("usage_bytes", 0), 0),
            "servers": raw.get("servers", []),
            "uuids": raw.get("uuids", {}),
            "created_at": raw.get("created_at") or u.utc_now_iso(),
            "expiry_date": raw.get("expiry_date") or ""
        }
        return user

    def create_user(self, data):
        uid = str(data.get("user_id", "")).strip()
        if not uid: return False, "User ID required"
        with self.user_lock:
            if any(u["user_id"].lower() == uid.lower() for u in self.users):
                return False, "User ID already exists"
            all_indices = list(range(len(self.servers)))
            uuids = {str(i): u.generate_uuid() for i in all_indices}
            new_user = self._normalize_user({
                "user_id": uid, "display_name": data.get("display_name", uid),
                "quota_gb": data.get("quota_gb", 0), "expiry_date": data.get("expiry_date", ""),
                "allow_direct": data.get("allow_direct", True), "allow_cdn": data.get("allow_cdn", True),
                "servers": all_indices, "uuids": uuids
            })
            self.users.append(new_user)
            self.save_users()
            self._update_xray_config_users()
            return True, new_user

    def delete_user(self, user_id):
        with self.user_lock:
            if user_id.lower() == "admin": return False, "Cannot delete admin"
            for i, user in enumerate(self.users):
                if user["user_id"].lower() == user_id.lower().strip():
                    self.users.pop(i)
                    self.save_users()
                    self._update_xray_config_users()
                    return True, "User deleted"
            return False, "User not found"

    def toggle_user(self, user_id, enabled):
        with self.user_lock:
            for user in self.users:
                if user["user_id"].lower() == user_id.lower().strip():
                    user["enabled"] = bool(enabled)
                    self.save_users()
                    self._update_xray_config_users()
                    return True, user
            return False, "User not found"

    def update_user(self, user_id, data):
        with self.user_lock:
            for user in self.users:
                if user["user_id"].lower() == user_id.lower().strip():
                    if "display_name" in data: user["display_name"] = str(data["display_name"]).strip()
                    if "quota_gb" in data: user["quota_gb"] = float(data["quota_gb"] or 0)
                    if "expiry_date" in data: user["expiry_date"] = str(data["expiry_date"]).strip()
                    if "allow_direct" in data: user["allow_direct"] = bool(data["allow_direct"])
                    if "allow_cdn" in data: user["allow_cdn"] = bool(data["allow_cdn"])
                    self.save_users()
                    self._update_xray_config_users()
                    return True, user
            return False, "User not found"

    def _read_inbound(self):
        path = os.path.join(self.CONFIG_DIR, "config.json")
        cfg = u.read_json_file(path)
        inbounds = cfg.get("inbounds", [])
        
        # Validate that the first inbound is actually a VLESS inbound with a port
        if inbounds and isinstance(inbounds[0], dict) and inbounds[0].get("port"):
            self.inbound = inbounds[0]
            if not self.inbound.get("tag"):
                self.inbound["tag"] = "vless_in"
                
            # Force maxEarlyData on existing configs
            ss = self.inbound.get("streamSettings", {})
            wss = ss.get("wsSettings", {})
            wss["maxEarlyData"] = 2048
            wss["earlyDataHeaderName"] = "Sec-WebSocket-Protocol"
            ss["wsSettings"] = wss
            self.inbound["streamSettings"] = ss
        else:
            self.inbound = self._get_default_inbound()


    def _get_default_inbound(self):
        s = self.settings
        return {
            "port": s.get("vless_port", 4433), 
            "listen": "0.0.0.0", 
            "protocol": "vless",
            "settings": {"clients": [], "decryption": "none"},
            "streamSettings": {
                "network": "ws", 
                "wsSettings": {
                    "path": s.get("vless_ws_path", "/tunnel"),
                    "maxEarlyData": 2048,
                    "earlyDataHeaderName": "Sec-WebSocket-Protocol"
                }
            }
        }

    def _get_user_email(self, user_id, server_index):
        # Sanitize user_id to be a valid email-like identifier (alphanumeric only)
        clean_id = re.sub(r'[^a-zA-Z0-9]', '', str(user_id))
        if not clean_id: clean_id = "user"
        return "{0}@s{1}.u".format(clean_id.lower(), server_index)

    def _update_xray_config_users(self):
        clients = []
        with self.user_lock:
            # Ensure the base setup UUID from settings.json is always authorized
            base_uuid = str(self.settings.get("vless_uuid", "")).lower().strip()
            if base_uuid:
                clients.append({
                    "id": base_uuid,
                    "level": 0,
                    "flow": "",
                    "email": "setup-admin@bridge.u"
                })

            for user in self.users:
                if not user.get("enabled", True): continue
                for i in range(len(self.servers)):
                    uid_for_server = user.get("uuids", {}).get(str(i))
                    if uid_for_server:
                        clients.append({"id": str(uid_for_server).lower().strip(), "level": 0, "flow": "", "email": self._get_user_email(user["user_id"], i)})
            u.log("INFO", "Injected {0} clients into Xray inbound.".format(len(clients)), component="XRAY")

        self.inbound["settings"]["clients"] = clients

        self.debounced_rebuild()

    def debounced_rebuild(self, immediate=False):
        if self._rebuild_timer: self._rebuild_timer.cancel()
        if immediate:
            self.rebuild_xray_config()
        else:
            self._rebuild_timer = threading.Timer(3.0, self.rebuild_xray_config)
            self._rebuild_timer.daemon = True
            self._rebuild_timer.start()

    def rebuild_xray_config(self):
        with self.user_lock:
            s = self.settings
            outbounds = []
            rules = []

            for i, server in enumerate(self.servers):
                tag = "outbound_s{0}".format(i)
                if server.get("outbound", {}).get("protocol") == "freedom":
                    outbounds.append({"tag": tag, "protocol": "freedom", "settings": {}})
                else:
                    out_cfg = server["outbound"].copy()
                    out_cfg["tag"] = tag
                    outbounds.append(out_cfg)

                user_emails = [ self._get_user_email(u_["user_id"], i) for u_ in self.users if u_.get("enabled", True) ]
                if user_emails:
                    rules.append({"type": "field", "user": user_emails, "outboundTag": tag})

            fastest_idx = 0
            min_ping = 99999
            for i_str, p in self.last_pings.items():
                try:
                    p_val = float(p)
                    if 0 < p_val < min_ping:
                        min_ping = p_val
                        fastest_idx = int(i_str)
                except: pass
            
            rules.insert(0, {"type": "field", "inboundTag": ["h_in", "s_in"], "outboundTag": "outbound_s{0}".format(fastest_idx)})
            rules.insert(0, {"type": "field", "inboundTag": ["api_in"], "outboundTag": "api"})

            vps_ip = s.get("vps_ip", "").strip()
            if vps_ip:
                rules.insert(0, {"type": "field", "outboundTag": "direct", "ip": [vps_ip]})

            rules.insert(1, {"type": "field", "outboundTag": "direct", "domain": ["regexp:\\.ir$"], "ip": ["geoip:ir"]})
            for i in range(len(self.servers)):
                rules.insert(0, {"type": "field", "user": ["t{0}".format(i)], "outboundTag": "outbound_s{0}".format(i)})

            rules.append({"type": "field", "outboundTag": "direct", "port": "0-65535"})
            outbounds.append({"tag": "direct", "protocol": "freedom", "settings": {}})

            xray_cfg = {
                "log": {"loglevel": "error"}, "stats": {},
                "api": {"tag": "api", "services": ["StatsService"]},
                "policy": {"levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}}},
                "inbounds": [
                    self.inbound,
                    {"tag": "h_in", "port": s.get("http_proxy_port", 1080), "listen": "127.0.0.1", "protocol": "http", "settings": {"auth": "noauth", "udp": True}},
                    {"tag": "s_in", "port": s.get("socks_proxy_port", 1082), "listen": "127.0.0.1", "protocol": "socks", "settings": {"auth": "noauth", "udp": True}},
                    {"tag": "api_in", "listen": "127.0.0.1", "port": 10085, "protocol": "dokodemo-door", "settings": {"address": "127.0.0.1"}},
                    {"tag": "test_in", "port": 10088, "listen": "127.0.0.1", "protocol": "http", "settings": {"accounts": [{"user": "t{0}".format(i), "pass": "p"} for i in range(len(self.servers))]}}
                ],
                "outbounds": outbounds,
                "routing": {"domainStrategy": "IPIfNonMatch", "rules": rules}
            }

            path = os.path.join(self.CONFIG_DIR, "config.json")
            if u.write_json_atomic(path, xray_cfg):
                self.save_state()
                u.log("OK", "Xray configuration updated.", component="XRAY")
                if self.process_manager: self.process_manager.trigger_restart("XRAY")
                return True, "Active"
            return False, "Write Failed"

    def switch_to(self, index):
        if index < 0 or index >= len(self.servers): return False, "Invalid index"
        self.active_index = index
        self.debounced_rebuild()
        return True, self.servers[index]["name"]

    def load_servers(self):
        path = os.path.join(self.CONFIG_DIR, "servers.json")
        data = u.read_json_file(path, {})
        self.servers = data.get("servers", [])
        for s in self.servers: s.setdefault("geo_done", False)
        self.active_index = data.get("active_index", 0)
        self.subscriptions = data.get("subscriptions", [])
        self.last_pings = data.get("last_pings", {})

        if not (self.servers and self.servers[0].get("outbound", {}).get("protocol") == "freedom"):
            freedom = {"name": "🇮🇷 🎓 Direct Connection", "flag": "🌐", "protocol": "freedom", "outbound": {"protocol": "freedom"}, "is_default": True}
            self.servers.insert(0, freedom)
            self.active_index = 0
        else:
            self.servers[0]["name"] = "🇮🇷 🎓 Direct Connection"
            self.servers[0]["is_default"] = True

    def save_state(self):
        u.write_json_file(os.path.join(self.CONFIG_DIR, "servers.json"), {
            "active_index": self.active_index, "servers": self.servers, 
            "subscriptions": self.subscriptions, "last_pings": self.last_pings
        })

    def resolve_all_geos(self):
        if self._geo_worker_running: return
        t = threading.Thread(target=self._geo_lookup_worker, name="GeoWorker")
        t.daemon = True
        t.start()

    def _get_server_ip(self, server):
        try:
            out = server.get("outbound", {})
            addr = out["settings"]["servers"][0]["address"] if out.get("protocol") == "shadowsocks" else out["settings"]["vnext"][0]["address"]
            return addr if re.match(r"^\d+\.\d+\.\d+\.\d+$", addr) else socket.gethostbyname(addr)
        except: return None

    def _geo_lookup_worker(self):
        with self._geo_lock:
            if self._geo_worker_running: return
            self._geo_worker_running = True
        
        try:
            changed_flags = {"changed": False}
            servers_to_check = []
            with self.user_lock:
                for i, s in enumerate(self.servers):
                    if s.get("is_default"): continue
                    
                    ip = self._get_server_ip(s)
                    if not ip or ip.startswith(("127.", "192.168.", "10.", "0.0.0.0")):
                        s["geo_done"] = True
                        continue
                        
                    # Force re-apply naming scheme if we already have this IP in cache
                    with self._geo_lock:
                        if ip in self._geo_cache:
                            self._apply_geo_to_server(i, self._geo_cache[ip])
                            continue
                            
                    if s.get("geo_done"): continue
                    servers_to_check.append((i, ip))

            if not servers_to_check: return

            u.log("INFO", "Starting Geo-lookup worker for {0} nodes...".format(len(servers_to_check)))

            def process_server(idx, ip):
                with self._geo_lock:
                    if ip in self._geo_cache:
                        self._apply_geo_to_server(idx, self._geo_cache[ip])
                        changed_flags["changed"] = True
                        return

                res = None
                apis = ["http://ip-api.com/json/{0}", "https://ipapi.co/{0}/json/", "https://ip-api.io/api/json/{0}"]
                for api_fmt in apis:
                    res = self._fetch_geo_data(api_fmt.format(ip))
                    if res: break
                    time.sleep(1.0)
                
                if res:
                    with self._geo_lock: self._geo_cache[ip] = res
                    self._apply_geo_to_server(idx, res)
                    self._sync_geo_by_ip(ip, res)
                    changed_flags["changed"] = True

            # Use ThreadPoolExecutor for geo-lookups
            futures = [self.executor.submit(process_server, i, ip) for i, ip in servers_to_check]
            for f in futures: f.result()

            if changed_flags["changed"]:
                self.save_state()
                self.debounced_rebuild()
            
            with self.user_lock:
                failures = sum(1 for s in self.servers if not s.get("geo_done") and not s.get("is_default"))
            if failures > 0:
                u.log("WARN", "Geo-lookup: {0} nodes pending. Retrying in 5m...".format(failures))
                threading.Timer(300.0, self.resolve_all_geos).start()
        finally:
            with self._geo_lock: self._geo_worker_running = False

    def _fetch_geo_data(self, url):
        try:
            import urllib.request as ur
            # SSL context is handled in utils.py
            req = ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = ur.urlopen(req, timeout=5).read().decode("utf-8")
            data = json.loads(resp)
            if data.get("countryCode") or data.get("country_code"): return data
        except:
            try:
                # Fallback via proxy
                proxy_url = "http://127.0.0.1:{0}".format(self.settings.get("http_proxy_port", 1080))
                opener = ur.build_opener(ur.ProxyHandler({'http': proxy_url, 'https': proxy_url}))
                resp = opener.open(url, timeout=10).read().decode("utf-8")
                data = json.loads(resp)
                if data.get("countryCode") or data.get("country_code"): return data
            except: pass
        return None

    def _apply_geo_to_server(self, idx, data):
        with self.user_lock:
            if idx >= len(self.servers): return
            s = self.servers[idx]
            cc = data.get("countryCode") or data.get("country_code", "??")
            country = data.get("country") or data.get("country_name", "Unknown")
            s["country"], s["country_code"] = country, cc
            if len(cc) == 2: s["flag"] = "".join(chr(127397 + ord(c)) for c in cc.upper())
            
            # Count how many servers from this country appear before this one
            count = 1
            for i in range(idx):
                if self.servers[i].get("country_code") == cc:
                    count += 1
            
            # Format: [flag] [country] #number
            s["name"] = "{0} {1} #{2}".format(s["flag"], country, count)
            s["geo_done"] = True

    def _sync_geo_by_ip(self, ip, data):
        with self.user_lock:
            for i, s in enumerate(self.servers):
                if self._get_server_ip(s) == ip: self._apply_geo_to_server(i, data)

    def add_server(self, name, flag, link):
        out = parse_config_link(link)
        if not out: return False, "Invalid link"
        self.servers.append({
            "name": name, 
            "original_name": name,
            "flag": flag or "🌐", 
            "protocol": out["protocol"], 
            "outbound": out, 
            "link": link
        })
        self._sync_user_uuids()
        self.save_state()
        self.resolve_all_geos()
        return True, name

    def remove_server(self, index):
        if index <= 0 or index >= len(self.servers) or self.servers[index].get("is_default"): return False
        self.servers.pop(index)
        if self.active_index >= index: self.active_index = max(0, self.active_index - 1)
        self.save_state()
        self._update_xray_config_users()
        return True

    def get_status(self):
        slist = [{"name": s["name"], "flag": s.get("flag", ""), "is_default": s.get("is_default", False), "protocol": s.get("protocol", s["outbound"].get("protocol", "")), "ping": self.last_pings.get(str(i)), "sub_url": s.get("sub_url")} for i, s in enumerate(self.servers)]
        
        # Calculate node counts
        total_nodes = len([s for s in self.servers if not s.get("is_default")])
        online_nodes = len([s for i, s in enumerate(self.servers) if not s.get("is_default") and self.last_pings.get(str(i), -1) > 0])
        
        return {
            "ok": True, 
            "active_index": self.active_index, 
            "servers": slist, 
            "tunnel_status": self.tunnel_status, 
            "tunnel_error": self.last_tunnel_error,
            "subscriptions": self.subscriptions,
            "dashboard_domain": self.dashboard_domain,
            "vps_ip": self.settings.get("vps_ip", ""),
            "ping_timeout_ms": self.ping_timeout_ms,            "max_log_size": self.settings.get("max_log_size", 100),
            "ping_interval_seconds": self.settings.get("ping_interval_seconds", 300),
            "system_stats": u.get_system_stats(),
            "node_counts": {"online": online_nodes, "total": total_nodes},
            "thresholds": self.settings.get("thresholds", {}),
            "is_pinging": getattr(self, "is_pinging", False),
            "ping_progress": getattr(self, "ping_progress", 0)
        }

    def ping_server(self, index):
        if index < 0 or index >= len(self.servers) or self.tunnel_status != "Connected":
            self.last_pings[str(index)] = -1
            return -1

        try:
            import urllib.request as ur
            proxy_url = "http://t{0}:p@127.0.0.1:10088".format(index)
            
            pings = []
            opener = ur.build_opener(ur.ProxyHandler({'http': proxy_url}))
            
            # Take 3 samples to check stability
            for _ in range(3):
                start = time.time()
                try:
                    with opener.open("https://www.gstatic.com/generate_204", timeout=self.ping_timeout_ms / 1000.0) as resp:
                        if resp.getcode() in [200, 204]:
                            pings.append(int((time.time() - start) * 1000))
                        else:
                            break # Bad response
                except Exception:
                    break # Timeout or connection error
                time.sleep(0.5) # Short delay between samples
                
            # Only consider the server healthy if all 3 pings succeeded
            if len(pings) == 3:
                # Use the worst ping of the 3 to penalize unstable servers
                ms = max(pings)
                self.last_pings[str(index)] = ms
                return ms
        except: pass
        
        self.last_pings[str(index)] = -1
        return -1

    def ping_all_servers(self):
        def monitor():
            if getattr(self, "is_pinging", False): return
            self.is_pinging = True
            self.ping_progress = 0
            u.log("INFO", "Mass ping cycle starting (batched)...", component="CORE")
            
            # To avoid saturating the local network and artificially inflating ping times,
            # we limit the number of concurrent pings.
            import concurrent.futures
            
            servers_to_ping = list(range(1, len(self.servers)))
            total = len(servers_to_ping)
            completed = 0
            
            if total > 0:
                with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pinger:
                    future_to_idx = {pinger.submit(self.ping_server, i): i for i in servers_to_ping}
                    for future in concurrent.futures.as_completed(future_to_idx):
                        try: future.result()
                        except: pass
                        completed += 1
                        self.ping_progress = int((completed / total) * 100)
                
            u.log("OK", "Mass ping cycle finished. Updating routing...", component="CORE")
            self.is_pinging = False
            self.ping_progress = 100
            self.debounced_rebuild()
        threading.Thread(target=monitor, name="MassPing").start()
        return True

    def _auto_ping_loop(self):
        import concurrent.futures
        while True:
            if self.tunnel_status == "Connected" and not getattr(self, "is_pinging", False):
                try:
                    self.is_pinging = True
                    self.ping_progress = 0
                    u.log("INFO", "Auto-ping cycle starting...", component="CORE")
                    
                    servers_to_ping = list(range(1, len(self.servers)))
                    total = len(servers_to_ping)
                    completed = 0
                    
                    if total > 0:
                        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pinger:
                            future_to_idx = {pinger.submit(self.ping_server, i): i for i in servers_to_ping}
                            for future in concurrent.futures.as_completed(future_to_idx):
                                try: future.result()
                                except: pass
                                completed += 1
                                self.ping_progress = int((completed / total) * 100)
                                
                    self.is_pinging = False
                    self.ping_progress = 100
                    self.save_state() 
                    self.debounced_rebuild()
                    u.log("OK", "Auto-ping cycle finished.", component="CORE")
                except Exception as e:
                    self.is_pinging = False
                    u.log("ERROR", "Auto-ping loop error: {0}".format(e))
            
            interval = self.settings.get("ping_interval_seconds", 300)
            # Ensure a minimum reasonable interval to avoid spamming
            if interval < 60: interval = 60
            time.sleep(interval)

    def add_subscription(self, url, name=""):
        if any(s["url"] == url for s in self.subscriptions): return False, "Already exists"
        self.subscriptions.append({"url": url, "name": name or url[:20], "last_update": "Never"})
        self.save_state()
        return self.update_subscription(url)

    def delete_subscription(self, url):
        self.subscriptions = [s for s in self.subscriptions if s["url"] != url]
        self.servers = [s for s in self.servers if s.get("sub_url") != url or s.get("is_default")]
        self.save_state()
        return True, "Subscription removed"

    def update_subscription(self, url):
        sub = next((s for s in self.subscriptions if s["url"] == url), None)
        if not sub: return False, "Sub not found"
        u.log("INFO", "Updating subscription: {0}".format(url))
        try:
            import urllib.request as ur
            req = ur.Request(url, headers={"User-Agent": "v2rayN/6.23"})
            text_body = ur.urlopen(req, timeout=15).read().decode("utf-8", "ignore").strip()
            if not text_body.startswith(("vless://", "vmess://", "ss://")):
                b64_data = "".join(text_body.split()).encode("utf-8")
                b64_data += b'=' * (4 - len(b64_data) % 4)
                text_body = base64.b64decode(b64_data).decode("utf-8")
            
            new_servers = []
            for line in text_body.splitlines():
                line = line.strip()
                if not line: continue
                out = parse_config_link(line)
                if out:
                    srv_name = unquote(line.split("#")[-1]) if "#" in line else "{0}_{1}".format(out["protocol"], len(new_servers))
                    new_servers.append({
                        "name": srv_name, 
                        "original_name": srv_name,
                        "flag": "🔗", 
                        "protocol": out["protocol"], 
                        "outbound": out, 
                        "link": line, 
                        "sub_url": url
                    })
            
            if new_servers:
                self.servers = [s for s in self.servers if s.get("sub_url") != url or s.get("is_default")]
                self.servers.extend(new_servers)
                sub["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                self._sync_user_uuids()
                self.save_state()
                self.resolve_all_geos()
                return True, "Added {0} servers".format(len(new_servers))
            return False, "No servers found"
        except Exception as e: return False, str(e)

    def _sync_user_uuids(self):
        with self.user_lock:
            for u_ in self.users:
                u_.setdefault("uuids", {})
                for i in range(len(self.servers)):
                    s_idx = str(i)
                    if s_idx not in u_["uuids"]: u_["uuids"][s_idx] = u.generate_uuid()
                    if i not in u_.get("servers", []): u_.setdefault("servers", []).append(i)
            self.save_users()
        self._update_xray_config_users()

    def _subscription_update_loop(self):
        while True:
            time.sleep(3600 * 6)
            for s in self.subscriptions: self.update_subscription(s["url"])

    def _user_maintenance_loop(self):
        while True:
            try:
                self._refresh_user_stats()
                self._enforce_user_limits()
            except Exception as e: u.log("ERROR", "Maint loop: {0}".format(e))
            time.sleep(30)

    def _refresh_user_stats(self):
        xray_bin = os.path.join(self.BIN_DIR, "xray.exe")
        if not os.path.exists(xray_bin): return
        try:
            cmd = [xray_bin, "api", "statsquery", "--server=127.0.0.1:10085"]
            res = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode("utf-8")
            stats = json.loads(res).get("stat", [])
            with self.user_lock:
                # Pre-calculate sanitized IDs for all users to speed up lookup
                user_map = {}
                for u_ in self.users:
                    # Same logic as _get_user_email's clean_id
                    clean_id = re.sub(r'[^a-zA-Z0-9]', '', str(u_["user_id"])).lower()
                    if not clean_id: clean_id = "user"
                    user_map[clean_id] = u_

                for entry in stats:
                    name, val = entry.get("name", ""), int(entry.get("value", 0))
                    if "user>>>" in name:
                        parts = name.split(">>>")
                        email, type_ = parts[1], parts[3]
                        # Email format is '{clean_id}@s{index}.u'
                        clean_id_from_email = email.split("@s")[0].strip().lower()
                        
                        key = "{0}_{1}".format(email, type_)
                        diff = val - self._last_xray_stats.get(key, 0)
                        if diff < 0: diff = val
                        self._last_xray_stats[key] = val
                        
                        target_user = user_map.get(clean_id_from_email)
                        if target_user:
                            target_user["usage_bytes"] = target_user.get("usage_bytes", 0) + diff
                self.save_users()
        except Exception as e:
            u.log("DEBUG", "Stats refresh failed: {0}".format(e), component="XRAY")

    def _enforce_user_limits(self):
        changed = False
        with self.user_lock:
            for u_ in self.users:
                if u_["user_id"].lower() == "admin": continue
                limit = int(u_.get("quota_gb", 0) * 1024**3)
                if (limit > 0 and u_.get("usage_bytes", 0) >= limit) or self._user_is_expired(u_):
                    if u_.get("enabled", True): u_["enabled"] = False; u.log("WARN", "User {0} disabled".format(u_["user_id"])); changed = True
        if changed: self.save_users(); self._update_xray_config_users()

    def _user_is_expired(self, user):
        exp = user.get("expiry_date", "")
        if not exp: return False
        try: return datetime.strptime(exp, "%Y-%m-%d").date() < datetime.utcnow().date()
        except: return False

    def _user_usage_bytes(self, user): return u.to_int(user.get("usage_bytes", 0))
    def _find_user_by_token(self, token):
        for u_ in self.users:
            if u_.get("token") == token: return u_
        return None

    def _subscription_url(self, token):
        domain, vps_ip = self.settings.get("dashboard_domain", ""), self.settings.get("vps_ip", "0.0.0.0")
        if not domain: return "http://{0}:8880/sub/{1}".format(vps_ip, token)
        panel_domain = domain if domain.startswith("panel.") else "panel." + domain
        return "https://{0}/sub/{1}".format(panel_domain, token)

    def _subscription_userinfo(self, user):
        used, total = self._user_usage_bytes(user), int(float(user.get("quota_gb", 0) or 0) * 1024**3)
        return "upload=0; download={0}; total={1}; expire=0".format(used, total)

    def _get_info_link(self, user):
        used_gb, total_gb = round(user.get("usage_bytes", 0) / (1024.0**3), 2), float(user.get("quota_gb", 0) or 0)
        rem_days = "∞"
        if user.get("expiry_date"):
            try: rem_days = str(max(0, (datetime.strptime(user["expiry_date"], "%Y-%m-%d").date() - datetime.utcnow().date()).days))
            except: pass
        remark = "👤 {0} | 📊 {1}/{2}GB | ⏳ {3} Days".format(user["user_id"], used_gb, total_gb, rem_days)
        return "vless://aaacbbc-cbaa-aabc-dacb-acbacbbcaacb@127.0.0.1:1080?encryption=none&security=tls&insecure=1&allowInsecure=1&type=tcp&headerType=none#{0}".format(quote(remark))

    def _make_vless_link(self, uuid, host, port, path, security, sni, remark):
        # Ensure path starts with / and is properly encoded, but avoid quoting the leading /
        if not path.startswith("/"): path = "/" + path
        # Simple path encoding: quote everything EXCEPT slashes
        safe_path = "".join([quote(c) if c != "/" else c for c in path])
        
        base = "vless://{0}@{1}:{2}".format(uuid, host, port)
        params = ["type=ws", "encryption=none", "path={0}".format(safe_path), "ed=2048"]
        if security == "tls":
            params.append("security=tls")
            if sni: params.append("sni={0}".format(sni))
            params.append("host={0}".format(sni if sni else host))
        else:
            params.append("security=none")
            params.append("host={0}".format(host))
            
        return "{0}?{1}#{2}".format(base, "&".join(params), quote(remark))

    def _build_user_subscription_links(self, user):
        links = [self._get_info_link(user)]
        ws_path, domain = self.settings.get("vless_ws_path", "/tunnel"), self.settings.get("dashboard_domain", "")
        country_counts = {}
        green, orange, red = [], [], []
        if 0 in user.get("servers", []) and user.get("user_id", "").lower() == "admin": green.append((0, 0))
        for s_idx in user.get("servers", []):
            if s_idx == 0: continue
            p = self.last_pings.get(str(s_idx), -1)
            if p < 0: continue
            if p < 300: green.append((s_idx, p))
            elif p < 800: orange.append((s_idx, p))
            else: red.append((s_idx, p))
        green.sort(key=lambda x: x[1]); orange.sort(key=lambda x: x[1]); red.sort(key=lambda x: x[1])
        indices = [x[0] for x in green] + [x[0] for x in orange] + [x[0] for x in red[:5]]

        for idx in indices:
            uuid_val = user.get("uuids", {}).get(str(idx))
            sd = self.servers[idx] if idx < len(self.servers) else None
            if not (uuid_val and sd): continue
            ping = self.last_pings.get(str(idx), 0)
            remark_base = sd.get("name", "Server")

            if user.get("allow_direct", True):
                remark = remark_base if idx == 0 else "{0} | {1} | ⚡ Bridge / {2}ms".format(remark_base, sd["protocol"].lower(), ping)
                links.append(self._make_vless_link(uuid_val, self.vps_ip, 8080, ws_path, "none", "", remark))
            if domain and user.get("allow_cdn", True):
                remark = remark_base if idx == 0 else "{0} | {1} | ☁️ CDN / {2}ms".format(remark_base, sd["protocol"].lower(), ping)
                links.append(self._make_vless_link(uuid_val, domain, 443, ws_path, "tls", domain, remark))
        return links

    def _build_user_dashboard_html(self, user, sub_url):
        usage, quota_gb = self._user_usage_bytes(user), float(user.get("quota_gb", 0) or 0)
        quota_bytes = int(quota_gb * 1024**3)
        pct = (usage / quota_bytes * 100) if quota_bytes > 0 else 0
        quota_text = "{0:.2f} GB".format(quota_gb) if quota_gb > 0 else "∞"
        
        rem_days = "∞"
        if user.get("expiry_date"):
            try: 
                diff = (datetime.strptime(user["expiry_date"], "%Y-%m-%d").date() - datetime.utcnow().date()).days
                rem_days = "{0} Days".format(max(0, diff)) if diff >= 0 else "Expired"
            except: pass

        vps_ip, domain, ws_path = self.vps_ip, self.settings.get("dashboard_domain", ""), self.settings.get("vless_ws_path", "/tunnel")
        
        filtered = [0] if (0 in user.get("servers", []) and user.get("user_id", "").lower() == "admin") else []
        green, yellow = [], []
        for i in user.get("servers", []):
            if i == 0: continue
            p = self.last_pings.get(str(i), -1)
            if p < 0: continue
            if p < 300: green.append((i, p))
            elif p < 800: yellow.append((i, p))
        green.sort(key=lambda x: x[1]); yellow.sort(key=lambda x: x[1])
        filtered.extend([x[0] for x in green] + [x[0] for x in yellow[:10]])

        servers_html = []
        for i in filtered:
            sd = self.servers[i] if i < len(self.servers) else None
            uuid_val = user.get("uuids", {}).get(str(i), "")
            if not (sd and uuid_val): continue
            s_name, btns = sd.get("name", "Server"), ""
            ping = self.last_pings.get(str(i), 0)
            if user.get("allow_direct", True):
                l = self._make_vless_link(uuid_val, vps_ip, 8080, ws_path, "none", "", s_name if i == 0 else "{0} | {1} | ⚡ Bridge / {2}ms".format(s_name, sd["protocol"].lower(), ping))
                btns += '<div class="action-btn tag-direct" onclick="copyText(\'{0}\')">Direct</div>'.format(l)
            if domain and user.get("allow_cdn", True):
                l = self._make_vless_link(uuid_val, domain, 443, ws_path, "tls", domain, s_name if i == 0 else "{0} | {1} | ☁️ CDN / {2}ms".format(s_name, sd["protocol"].lower(), ping))
                btns += '<div class="action-btn tag-cdn" onclick="copyText(\'{0}\')">CDN</div>'.format(l)
            if btns: servers_html.append('<div class="server-card"><div class="server-meta"><span class="server-icon">{0}</span><span class="server-title">{1}</span></div><div class="server-actions">{2}</div></div>'.format(sd.get("flag", "🌐"), s_name, btns))

        return self._get_dashboard_template().format(
            name=html_escape(user.get("display_name", "User")), 
            used_gb=usage/(1024.0**3), 
            quota_text=quota_text, 
            rem_days=rem_days,
            pct=min(100, pct) if quota_bytes > 0 else 0, 
            server_blocks="".join(servers_html), 
            sub_url=sub_url, 
            sub_url_quoted=quote(sub_url)
        )

    def _get_dashboard_template(self):
        return """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>AUT Relay | Access Portal</title>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{ --bg: #030406; --card: #0a0c12; --border: rgba(255,255,255,0.08); --text: #f8fafc; --muted: #94a3b8; --accent: #3b82f6; --success: #10b981; --glass: rgba(255, 255, 255, 0.03); }}
        * {{ margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color: transparent; }}
        body {{ background: var(--bg); color: var(--text); font-family: 'Plus Jakarta Sans', sans-serif; padding: 20px; min-height: 100vh; display: flex; justify-content: center; background-image: radial-gradient(circle at 10% 10%, rgba(59, 130, 246, 0.05) 0%, transparent 40%), radial-gradient(circle at 90% 90%, rgba(16, 185, 129, 0.05) 0%, transparent 40%); }}
        .container {{ width: 100%; max-width: 440px; animation: fadeIn 0.6s ease-out; }}
        @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: translateY(0); }} }}
        .header-card {{ background: linear-gradient(165deg, #0e121a 0%, #050609 100%); border-radius: 24px; padding: 24px; margin-bottom: 20px; border: 1px solid var(--border); position: relative; box-shadow: 0 10px 30px rgba(0,0,0,0.4); }}
        .header-card::after {{ content: ''; position: absolute; top: 0; left: 0; right: 0; height: 1px; background: linear-gradient(90deg, transparent, var(--accent), transparent); }}
        .user-info {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }}
        .user-name {{ font-size: 20px; font-weight: 800; letter-spacing: -0.02em; }}
        .status-badge {{ background: rgba(16, 185, 129, 0.1); color: var(--success); padding: 5px 10px; border-radius: 8px; font-size: 11px; font-weight: 800; text-transform: uppercase; border: 1px solid rgba(16, 185, 129, 0.2); letter-spacing: 0.5px; }}
        .stats-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 20px; }}
        .stat-item {{ background: var(--glass); padding: 14px; border-radius: 16px; border: 1px solid var(--border); }}
        .stat-label {{ font-size: 10px; color: var(--muted); margin-bottom: 4px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; }}
        .stat-value {{ font-size: 15px; font-weight: 700; }}
        .progress-bar {{ height: 6px; background: var(--glass); border-radius: 10px; overflow: hidden; margin: 10px 0; }}
        .progress-fill {{ height: 100%; background: linear-gradient(90deg, #3b82f6, #60a5fa); box-shadow: 0 0 12px rgba(59, 130, 246, 0.4); transition: width 1.5s cubic-bezier(0.4, 0, 0.2, 1); }}
        .btn-sub {{ width: 100%; background: #fff; color: #000; border: none; padding: 14px; border-radius: 14px; font-weight: 700; font-size: 14px; cursor: pointer; margin-top: 8px; transition: all 0.2s; box-shadow: 0 4px 12px rgba(255,255,255,0.1); }}
        .btn-sub:active {{ transform: scale(0.97); }}
        .server-grid {{ display: flex; flex-direction: column; gap: 10px; }}
        .server-card {{ background: var(--card); border-radius: 16px; padding: 14px 18px; border: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; transition: transform 0.2s; }}
        .server-card:hover {{ transform: translateX(4px); border-color: rgba(255,255,255,0.15); }}
        .server-meta {{ display: flex; align-items: center; gap: 12px; }}
        .server-icon {{ font-size: 20px; }}
        .server-title {{ font-size: 14px; font-weight: 600; color: #eee; }}
        .server-actions {{ display: flex; gap: 8px; }}
        .action-btn {{ font-size: 10px; font-weight: 800; text-transform: uppercase; padding: 6px 12px; border-radius: 8px; cursor: pointer; border: 1px solid rgba(255,255,255,0.05); transition: all 0.2s; }}
        .action-btn:active {{ transform: scale(0.92); }}
        .tag-direct {{ background: rgba(59, 130, 246, 0.1); color: #60a5fa; }}
        .tag-cdn {{ background: rgba(245, 158, 11, 0.1); color: #fbbf24; }}
        .toast {{ position: fixed; bottom: 32px; left: 50%; transform: translateX(-50%) translateY(100px); background: #fff; color: #000; padding: 12px 24px; border-radius: 12px; font-size: 13px; font-weight: 700; transition: transform 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275); z-index: 1000; box-shadow: 0 10px 30px rgba(0,0,0,0.4); }}
        .toast.show {{ transform: translateX(-50%) translateY(0); }}
        .qr-card {{ background: #fff; border-radius: 20px; padding: 16px; margin-top: 20px; text-align: center; display: flex; flex-direction: column; align-items: center; gap: 12px; }}
        .qr-card img {{ width: 180px; height: 180px; }}
        .qr-label {{ color: #666; font-size: 11px; font-weight: 700; text-transform: uppercase; }}
    </style></head><body><div class="container"><div class="header-card"><div class="user-info"><h1 class="user-name">{name}</h1><span id="status_pill" class="status-badge">Active</span></div>
    <div class="stats-grid"><div class="stat-item"><p class="stat-label">Data Consumed</p><p id="usage_text" class="stat-value">{used_gb:.2f} GB</p></div>
    <div class="stat-item"><p class="stat-label">Total Limit</p><p class="stat-value">{quota_text}</p></div></div>
    <div class="stats-grid" style="margin-top:-10px;"><div class="stat-item" style="grid-column: span 2;"><p class="stat-label">Validity Remaining</p><p class="stat-value">⏳ {rem_days}</p></div></div>
    <div class="progress-bar"><div id="progress_fill" class="progress-fill" style="width: {pct}%"></div></div>
    <button class="btn-sub" onclick="copyText('{sub_url}')">Copy Subscription URL</button></div>
    <div class="server-grid">{server_blocks}</div>
    <div class="qr-card"><img src="https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={sub_url_quoted}" alt="QR"><p class="qr-label">Scan to import to V2Ray / Shadowrocket</p></div>
    </div><div id="toast" class="toast">Link copied to clipboard</div>
    <script>
        function copyText(t){{ if (navigator.clipboard) {{ navigator.clipboard.writeText(t).then(showToast); }} else {{ var i = document.createElement("input"); i.value = t; document.body.appendChild(i); i.select(); document.execCommand("copy"); document.body.removeChild(i); showToast(); }} }}
        function showToast() {{ let s=document.getElementById('toast'); s.classList.add('show'); setTimeout(()=>{{s.classList.remove('show');}},2500); }}
        function formatBytes(b){{ if(!b)return '0 B'; var k=1024,sizes=['B','KB','MB','GB','TB'],i=Math.floor(Math.log(b)/Math.log(k)); return parseFloat((b/Math.pow(k,i)).toFixed(2))+' '+sizes[i]; }}
        function refresh(){{ fetch('{sub_url}/stats').then(r=>r.json()).then(d=>{{
            var u=d.used_bytes||0, q=d.quota_bytes||0;
            document.getElementById('usage_text').textContent=formatBytes(u);
            document.getElementById('progress_fill').style.width=(q?Math.min(100,(u/q)*100):0)+'%';
            var p=document.getElementById('status_pill'); p.textContent=d.enabled?'Active':'Disabled'; p.style.color=d.enabled?'#10b981':'#ef4444';
        }}).catch(e=>{{}}); }}
        refresh(); setInterval(refresh, 30000);
    </script></body></html>"""

    def perform_initial_setup(self, data):
        """Used by setup_server.py to generate first-time configs."""
        with self.user_lock:
            u.log("INFO", "Starting initial setup with data: {0}".format(json.dumps({k: (v if 'pass' not in k.lower() else '***') for k, v in data.items()})))
            try:
                frp_token, vless_uuid = u.generate_token(24), u.generate_uuid()
                
                # 1. settings.json
                s = {
                    "vps_ip": data["vps_ip"], 
                    "frp_server_port": int(data.get("frp_server_port", 443)), 
                    "frp_protocol": data.get("frp_protocol", "tcp"),
                    "frp_token": frp_token, "vless_uuid": vless_uuid, "vless_port": 4433, "vless_ws_path": "/tunnel",
                    "management_port": 3080, "http_proxy_port": 1080, "socks_proxy_port": 1082, "remote_xray_port": 8080,
                    "remote_dashboard_port": 8880, "remote_http_port": 1081, "remote_socks_port": 1083, 
                    "dashboard_user": data["dash_user"], "dashboard_pass": data["dash_pass"],
                    "dashboard_domain": data.get("dashboard_domain", ""),
                    "portal_url": "https://internet.aut.ac.ir",
                    "check_interval_seconds": 60,
                    "thresholds": {"daily_gb": 2.8, "weekly_gb": 11.5, "monthly_gb": 29.0, "session_gb": 1.0}
                    }

                self.settings = s
                u.write_json_file(os.path.join(self.CONFIG_DIR, "settings.json"), s)
                u.log("OK", "Settings saved.")
                
                # 2. accounts.json
                accounts_cfg = {
                    "accounts": [{"username": data["aut_user"], "password": data["aut_pass"]}],
                    "thresholds": s["thresholds"],
                    "check_interval_seconds": s["check_interval_seconds"],
                    "portal_url": s["portal_url"]
                }
                u.write_json_file(os.path.join(self.CONFIG_DIR, "accounts.json"), accounts_cfg)
                u.log("OK", "Accounts saved.")
                
                # 3. servers.json (Initial state)
                servers_cfg = {
                    "active_index": 0,
                    "servers": [{"name": "🇮🇷 🎓 Direct Connection", "flag": "🌐", "protocol": "freedom", "outbound": {"protocol": "freedom"}, "is_default": True}],
                    "subscriptions": [],
                    "last_pings": {}
                }
                u.write_json_file(os.path.join(self.CONFIG_DIR, "servers.json"), servers_cfg)
                u.log("OK", "Servers saved.")
                
                # 4. Initialize user database (admin)
                self.load_servers()
                self.users = self.load_users()
                u.log("OK", "User database initialized.")

                # 5. Generate local binary configs
                self.inbound = self._get_default_inbound()
                self._update_xray_config_users()  # Populate clients from user database
                self._update_local_frp_config(restart=False)
                self.rebuild_xray_config()
                u.log("OK", "Configs generated.")
                
                frps_cfg = [
                    'bindPort = {0}'.format(s["frp_server_port"]),
                    'auth.method = "token"', 'auth.token = "{0}"'.format(frp_token),
                    'transport.tls.force = true'
                ]
                if s.get("dashboard_domain"):
                    frps_cfg.append('vhostHTTPSPort = 443')

                vless_link = self._make_vless_link(vless_uuid, s["vps_ip"], 8080, s["vless_ws_path"], "none", "", "AUT-Bridge")
                u.log("OK", "Setup process complete.")
                return vless_link, frp_token, "\n".join(frps_cfg)
            except Exception as e:
                u.log("ERROR", "Initial setup failed: {0}".format(e))
                import traceback
                traceback.print_exc()
                raise e

    def add_aut_account(self, username, password):
        path = os.path.join(self.CONFIG_DIR, "accounts.json")
        try:
            data = u.read_json_file(path, {"accounts": []})
            data["accounts"].append({"username": username, "password": password})
            u.write_json_file(path, data); self.aut_accounts.append({"username": username, "password": password}); return True
        except: return False

    def remove_aut_account(self, username):
        path = os.path.join(self.CONFIG_DIR, "accounts.json")
        try:
            data = u.read_json_file(path, {"accounts": []})
            data["accounts"] = [a for a in data["accounts"] if a["username"] != username]
            u.write_json_file(path, data); self.aut_accounts = [a for a in self.aut_accounts if a["username"] != username]; return True
        except: return False

    def request_aut_switch(self, index):
        self.force_switch = index; self.wake_event.set()
    def consume_force_switch(self):
        idx = self.force_switch; self.force_switch = -1; return idx
    def wait_for_interval(self, seconds):
        self.wake_event.wait(timeout=seconds); self.wake_event.clear()
