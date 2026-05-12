# -*- coding: utf-8 -*-
import os
import sys

# Add core directory to path for modules
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "core"))

import time
import webbrowser
import threading

import utils as u
from server_manager import ServerManager
from setup_handler import SetupHandler
from web_server import start_server

def do_reset(config_dir, base_dir):
    """Wipe all generated configuration files."""
    print("\n" + "=" * 56 + "\n       AUT Proxy Bridge - Complete Reset\n" + "=" * 56 + "\n")
    print("  This will DELETE all configuration files:")
    for f in ["settings.json", "accounts.json", "accounts_usage.json", "servers.json", "config.json", "frpc.toml", "frpc.ini", "xray.log"]:
        print("    - {0}".format(f))
    
    try:
        confirm = input("\n  Type 'RESET' to confirm: ").strip()
    except EOFError: confirm = ""
    
    if confirm != "RESET":
        print("  Cancelled."); return

    for f in ["settings.json", "accounts.json", "accounts_usage.json", "servers.json", "config.json", "frpc.toml", "frpc.ini", "xray.log"]:
        path = os.path.join(config_dir, f)
        try:
            if os.path.exists(path): os.remove(path); print("  Deleted: {0}".format(f))
        except Exception as e: print("  Error deleting {0}: {1}".format(f, e))

    print("\n  Reset complete. Run Setup.bat to reconfigure.\n")

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_dir = os.path.join(base_dir, "config")
    
    if len(sys.argv) > 1 and sys.argv[1] in ("--reset", "-r", "reset"):
        do_reset(config_dir, base_dir); return

    if not os.path.exists(config_dir): os.makedirs(config_dir)

    settings_path = os.path.join(config_dir, "settings.json")
    if os.path.exists(settings_path):
        print("\n  Configuration already exists.")
        try: choice = input("  (R)econfigure / (C)ancel [C]: ").strip().lower()
        except EOFError: choice = "c"
        if choice not in ("r", "reconfigure"):
            print("  Cancelled. Current config unchanged."); sys.exit(0)
        
    mgr = ServerManager() # Initialized in setup mode (defaults)
    port = mgr.settings.get("management_port", 3080)
    
    print("=" * 44 + "\n  AUT Proxy Bridge - Web Setup Started\n" + "=" * 44 + "\n")
    print("  Please open your browser and navigate to:\n  http://127.0.0.1:{0}\n".format(port))
    print("  Waiting for configuration...")
    
    def _open_browser():
        time.sleep(1.5); webbrowser.open("http://127.0.0.1:{0}".format(port))
    threading.Thread(target=_open_browser).start()

    # Start the setup server (blocks)
    from web_server import HTTPServer
    server = HTTPServer(("0.0.0.0", port), SetupHandler)
    server.mgr = mgr
    server.setup_done = False
    server.serve_forever()

if __name__ == "__main__":
    main()
