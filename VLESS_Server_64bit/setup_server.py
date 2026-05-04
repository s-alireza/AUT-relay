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
import random
import string
try:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from urllib.parse import quote
except ImportError:
    from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
    from urllib import quote

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
UI_DIR = os.path.join(BASE_DIR, "ui")

# Architecture-specific FRP config format
FRPC_FORMAT = "toml"  # "toml" for 64-bit, "ini" for 32-bit

DEFAULTS = {
    "frp_server_port": 443,
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

def generate_token(length=32):
    """Generate a random alphanumeric token for FRP authentication."""
    try:
        rng = random.SystemRandom()
    except Exception:
        rng = random
    chars = string.ascii_letters + string.digits
    return ''.join(rng.choice(chars) for _ in range(length))

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
            'auth.method = "token"',
            'auth.token = "{0}"'.format(settings.get("frp_token", "")),
            'transport.tls.enable = true',
            'transport.tls.serverName = "www.google.com"',
            'transport.tls.disableCustomTLSFirstByte = true',
            'transport.heartbeatInterval = 10',
            'transport.heartbeatTimeout = 30',
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
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    else:
        path = os.path.join(CONFIG_DIR, "frpc.ini")
        lines = [
            '[common]',
            'server_addr = {0}'.format(settings["vps_ip"]),
            'server_port = {0}'.format(settings["frp_server_port"]),
            'protocol = {0}'.format(settings["frp_protocol"]),
            'authenticate_heartbeats = true',
            'authenticate_new_work_conns = true',
            'token = {0}'.format(settings.get("frp_token", "")),
            'tls_enable = true',
            'heartbeat_interval = 10',
            'heartbeat_timeout = 30',
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
        with open(path, "w", encoding="utf-8") as f:
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
    settings["frp_token"] = generate_token(32)
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

    frps_cfg = [
        'bindPort = {0}'.format(settings["frp_server_port"]),
        'auth.method = "token"',
        'auth.token = "{0}"'.format(settings["frp_token"]),
        'transport.tls.force = true'
    ]
    
    ws_path_encoded = quote(settings["vless_ws_path"])
    vless_link = "vless://{0}@{1}:{2}?type=ws&path={3}#AUT-Bridge".format(
        settings["vless_uuid"],
        settings["vps_ip"],
        settings["remote_xray_port"],
        ws_path_encoded
    )
    return vless_link, settings["frp_token"], "\n".join(frps_cfg)

server_instance = None
setup_done = False

class SetupHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, data, code=200):
        body = json.dumps(data).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    def _send_html(self, html):
        body = html.encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        if self.path == "/":
            if setup_done:
                # Show a transitioning page if setup is already complete
                self._send_html("""
                <html>
                <head>
                    <meta http-equiv="refresh" content="3;url=/">
                    <style>
                        body { background: #0f172a; color: white; font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
                        .box { text-align: center; background: rgba(255,255,255,0.05); padding: 40px; border-radius: 20px; border: 1px solid rgba(255,255,255,0.1); }
                        .loader { border: 3px solid rgba(255,255,255,0.1); border-top: 3px solid #3b82f6; border-radius: 50%; width: 30px; height: 30px; animation: spin 1s linear infinite; margin: 0 auto 20px; }
                        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
                    </style>
                </head>
                <body>
                    <div class="box">
                        <div class="loader"></div>
                        <h2>🚀 Finalizing Setup...</h2>
                        <p>Transitioning to the Dashboard. Please wait a moment.</p>
                    </div>
                </body>
                </html>
                """)
                return

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
                vless_link, frp_token, frps_toml = do_setup(data)
                self._send_json({"ok": True, "vless_link": vless_link, "frp_token": frp_token, "frps_toml": frps_toml})
                
                # Shutdown the server slightly after responding so the client gets the success message
                def shutdown():
                    global setup_done
                    setup_done = True
                    print("\n  Setup completed successfully. Transitioning to dashboard...")
                    time.sleep(2)
                    if server_instance:
                        server_instance.shutdown()
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
            # Check for backward compatibility: inject token if missing
            try:
                with open(settings_path, "r", encoding="utf-8") as f:
                    curr = json.load(f)
                if "frp_token" not in curr:
                    print("  Upgrading configuration with security token...")
                    curr["frp_token"] = generate_token(32)
                    # Migrate port if it's the old default
                    if curr.get("frp_server_port") == 7000:
                        curr["frp_server_port"] = 8443
                    write_json(settings_path, curr)
                    generate_frpc(curr)
                    print("  Upgrade complete. Token added and port migrated to 8443.")
            except Exception as e:
                print("  Warning during auto-upgrade: {0}".format(e))
            
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

def start_setup(mgr=None):
    """Entry point for account_manager."""
    global _mgr, server_instance
    _mgr = mgr
    port = DEFAULTS["management_port"]
    server_instance = HTTPServer(("0.0.0.0", port), SetupHandler)
    print("\n[INFO] Starting Web Setup on port {0}...".format(port))
    server_instance.serve_forever()
    print("[INFO] Web Setup finished.")

if __name__ == "__main__":
    main()
