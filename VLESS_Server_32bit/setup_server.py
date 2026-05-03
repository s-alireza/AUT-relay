# -*- coding: utf-8 -*-
"""
AUT Proxy Bridge - Web Setup Server
Serves the initial setup UI and generates configuration files.
"""
import json
import os
import sys
import uuid as _uuid
import threading
import time
try:
    from http.server import HTTPServer, BaseHTTPRequestHandler
except ImportError:
    from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
UI_DIR = os.path.join(BASE_DIR, "ui")

# Architecture-specific FRP config format
FRPC_FORMAT = "ini"  # "toml" for 64-bit, "ini" for 32-bit

DEFAULTS = {
    "frp_server_port": 7000,
    "frp_protocol": "tcp",
    "vless_ws_path": "/tunnel",
    "vless_port": 4433,
    "management_port": 3080,
    "http_proxy_port": 1080,
    "socks_proxy_port": 1082,
    "remote_xray_port": 8080,
    "remote_dashboard_port": 8880,
    "remote_http_port": 1081,
    "remote_socks_port": 1083,
    "portal_url": "https://internet.aut.ac.ir",
    "check_interval_seconds": 60,
    "thresholds": {
        "daily_gb": 2.8,
        "weekly_gb": 11.5,
        "monthly_gb": 29.0,
        "free_gb": 115.0
    }
}

def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def generate_frpc(settings):
    """Generate FRP client config file."""
    if FRPC_FORMAT == "toml":
        path = os.path.join(CONFIG_DIR, "frpc.toml")
        lines = [
            'serverAddr = "{0}"'.format(settings["vps_ip"]),
            'serverPort = {0}'.format(settings["frp_server_port"]),
            'transport.protocol = "{0}"'.format(settings["frp_protocol"]),
            'loginFailExit = false',
            '',
            '[[proxies]]',
            'name = "xray_ws"',
            'type = "tcp"',
            'localIP = "127.0.0.1"',
            'localPort = {0}'.format(settings["vless_port"]),
            'remotePort = {0}'.format(settings["remote_xray_port"]),
            '',
            '[[proxies]]',
            'name = "dashboard"',
            'type = "tcp"',
            'localIP = "127.0.0.1"',
            'localPort = {0}'.format(settings["management_port"]),
            'remotePort = {0}'.format(settings["remote_dashboard_port"]),
            '',
            '[[proxies]]',
            'name = "http_tunnel"',
            'type = "tcp"',
            'localIP = "127.0.0.1"',
            'localPort = {0}'.format(settings["http_proxy_port"]),
            'remotePort = {0}'.format(settings["remote_http_port"]),
            '',
            '[[proxies]]',
            'name = "socks_tunnel"',
            'type = "tcp"',
            'localIP = "127.0.0.1"',
            'localPort = {0}'.format(settings["socks_proxy_port"]),
            'remotePort = {0}'.format(settings["remote_socks_port"]),
            '',
        ]
        with open(path, "w") as f:
            f.write("\n".join(lines))
    else:
        path = os.path.join(CONFIG_DIR, "frpc.ini")
        lines = [
            '[common]',
            'server_addr = {0}'.format(settings["vps_ip"]),
            'server_port = {0}'.format(settings["frp_server_port"]),
            'protocol = {0}'.format(settings["frp_protocol"]),
            'login_fail_exit = false',
            '',
            '[xray_ws]',
            'type = tcp',
            'local_ip = 127.0.0.1',
            'local_port = {0}'.format(settings["vless_port"]),
            'remote_port = {0}'.format(settings["remote_xray_port"]),
            '',
            '[dashboard]',
            'type = tcp',
            'local_ip = 127.0.0.1',
            'local_port = {0}'.format(settings["management_port"]),
            'remote_port = {0}'.format(settings["remote_dashboard_port"]),
            '',
            '[http_tunnel]',
            'type = tcp',
            'local_ip = 127.0.0.1',
            'local_port = {0}'.format(settings["http_proxy_port"]),
            'remote_port = {0}'.format(settings["remote_http_port"]),
            '',
            '[socks_tunnel]',
            'type = tcp',
            'local_ip = 127.0.0.1',
            'local_port = {0}'.format(settings["socks_proxy_port"]),
            'remote_port = {0}'.format(settings["remote_socks_port"]),
            '',
        ]
        with open(path, "w") as f:
            f.write("\n".join(lines))

def generate_xray_config(settings):
    """Generate the initial Xray config (Freedom outbound)."""
    xray_cfg = {
        "inbounds": [
            {
                "port": settings["vless_port"],
                "listen": "0.0.0.0",
                "protocol": "vless",
                "settings": {
                    "clients": [{"id": settings["vless_uuid"], "flow": ""}],
                    "decryption": "none"
                },
                "streamSettings": {
                    "network": "ws",
                    "wsSettings": {"path": settings["vless_ws_path"]}
                }
            },
            {
                "tag": "http-inbound",
                "port": settings["http_proxy_port"],
                "listen": "127.0.0.1",
                "protocol": "http",
                "settings": {"auth": "noauth", "udp": True}
            },
            {
                "tag": "socks-inbound",
                "port": settings["socks_proxy_port"],
                "listen": "127.0.0.1",
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": True}
            }
        ],
        "outbounds": [
            {"tag": "proxy", "protocol": "freedom", "settings": {}},
            {"tag": "direct", "protocol": "freedom", "settings": {}}
        ],
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                {
                    "type": "field",
                    "outboundTag": "direct",
                    "ip": [settings["vps_ip"]]
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
    }
    write_json(os.path.join(CONFIG_DIR, "config.json"), xray_cfg)

def do_setup(data):
    vps_ip = data.get("vps_ip", "").strip()
    aut_user = data.get("aut_user", "").strip()
    aut_pass = data.get("aut_pass", "").strip()
    dash_user = data.get("dash_user", "").strip()
    dash_pass = data.get("dash_pass", "").strip()

    if not all([vps_ip, aut_user, aut_pass, dash_user, dash_pass]):
        raise ValueError("Missing required fields")

    settings = dict(DEFAULTS)
    settings["vps_ip"] = vps_ip
    settings["vless_uuid"] = str(_uuid.uuid4())
    settings["dashboard_user"] = dash_user
    settings["dashboard_pass"] = dash_pass

    if not os.path.exists(CONFIG_DIR):
        os.makedirs(CONFIG_DIR)

    # 1. settings.json
    write_json(os.path.join(CONFIG_DIR, "settings.json"), settings)

    # 2. accounts.json
    accounts_cfg = {
        "accounts": [{"username": aut_user, "password": aut_pass}],
        "thresholds": settings["thresholds"],
        "check_interval_seconds": settings["check_interval_seconds"],
        "portal_url": settings["portal_url"]
    }
    write_json(os.path.join(CONFIG_DIR, "accounts.json"), accounts_cfg)

    # 3. accounts_usage.json
    write_json(os.path.join(CONFIG_DIR, "accounts_usage.json"), {"accounts": {}})

    # 4. servers.json
    servers_cfg = {
        "active_index": 0,
        "management_port": settings["management_port"],
        "servers": [
            {"name": "Freedom", "flag": "", "outbound": {"protocol": "freedom"}}
        ]
    }
    write_json(os.path.join(CONFIG_DIR, "servers.json"), servers_cfg)

    # 5. config.json (Xray)
    generate_xray_config(settings)

    # 6. FRPC config
    generate_frpc(settings)

    ws_path_encoded = settings["vless_ws_path"].replace("/", "%2F")
    vless_link = "vless://{0}@{1}:{2}?type=ws&path={3}#AUT-Bridge".format(
        settings["vless_uuid"],
        settings["vps_ip"],
        settings["remote_xray_port"],
        ws_path_encoded
    )
    return vless_link

server_instance = None

class SetupHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, data, code=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
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

    def do_GET(self):
        if self.path == "/":
            html_path = os.path.join(UI_DIR, "setup.html")
            try:
                with open(html_path, "r", encoding="utf-8") as f:
                    self._send_html(f.read())
            except Exception:
                self._send_html("<h1>setup.html not found</h1>")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/setup":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                data = json.loads(body)
                vless_link = do_setup(data)
                self._send_json({"ok": True, "vless_link": vless_link})
                
                # Shutdown the server slightly after responding so the client gets the success message
                def shutdown():
                    print("\n  Setup completed successfully. Closing setup server...")
                    time.sleep(2)
                    os._exit(0)
                threading.Thread(target=shutdown).start()
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)
        else:
            self.send_response(404)
            self.end_headers()

def do_reset():
    """Wipe all generated configuration files."""
    print()
    print("=" * 56)
    print("       AUT Proxy Bridge - Complete Reset")
    print("=" * 56)
    print()
    print("  This will DELETE all configuration files:")
    print("    - Account credentials (accounts.json)")
    print("    - Usage history (accounts_usage.json)")
    print("    - Server configurations (servers.json)")
    print("    - Xray config (config.json)")
    print("    - FRP config (frpc.toml / frpc.ini)")
    print("    - Infrastructure settings (settings.json)")
    print("    - Logs (xray.log, out.log)")
    print()

    confirm = input("  Type 'RESET' to confirm: ").strip()
    if confirm != "RESET":
        print("  Cancelled.")
        return

    print()
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
                print("  Deleted: {0}".format(os.path.relpath(fpath, BASE_DIR)))
        except Exception as e:
            print("  Error: {0} — {1}".format(os.path.basename(fpath), e))

    cache_dir = os.path.join(BASE_DIR, "__pycache__")
    if os.path.exists(cache_dir):
        import shutil
        try:
            shutil.rmtree(cache_dir)
            print("  Deleted: __pycache__/")
        except Exception:
            pass

    print()
    print("  Reset complete. Run Setup.bat to reconfigure.")
    print()

def main():
    global server_instance
    if len(sys.argv) > 1 and sys.argv[1] in ("--reset", "-r", "reset"):
        do_reset()
        return

    settings_path = os.path.join(CONFIG_DIR, "settings.json")
    if os.path.exists(settings_path):
        print()
        print("  Configuration already exists.")
        try:
            choice = input("  (R)econfigure / (C)ancel [C]: ").strip().lower()
        except EOFError:
            choice = "c"
        if choice not in ("r", "reconfigure"):
            print("  Cancelled. Your current config is unchanged.")
            sys.exit(0)
        
    port = DEFAULTS["management_port"]
    server_instance = HTTPServer(("0.0.0.0", port), SetupHandler)
    print("============================================")
    print("  AUT Proxy Bridge - Web Setup Started")
    print("============================================")
    print()
    print("  Please open your browser and navigate to:")
    print("  http://127.0.0.1:{0}".format(port))
    print()
    print("  Waiting for configuration...")
    server_instance.serve_forever()

if __name__ == "__main__":
    main()
