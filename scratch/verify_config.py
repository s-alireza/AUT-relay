import sys
import os
import json

# Add core to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "core")))

from server_manager import ServerManager

def verify():
    print("Initializing ServerManager...")
    # Mocking process manager to avoid actual process starts
    sm = ServerManager(process_manager=None)
    
    print("Triggering config rebuild...")
    sm.rebuild_xray_config()
    
    config_path = os.path.join(sm.CONFIG_DIR, "config.json")
    if not os.path.exists(config_path):
        print("ERROR: config.json not found!")
        return
        
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
        
    outbounds = cfg.get("outbounds", [])
    api_outbound = next((o for o in outbounds if o.get("tag") == "api"), None)
    
    if api_outbound:
        print("SUCCESS: 'api' outbound found in configuration.")
    else:
        print("FAILURE: 'api' outbound NOT found in configuration.")
        
    # Check api_in routing
    rules = cfg.get("routing", {}).get("rules", [])
    api_rule = next((r for r in rules if "api_in" in r.get("inboundTag", []) and r.get("outboundTag") == "api"), None)
    
    if api_rule:
        print("SUCCESS: Routing rule for 'api_in' found.")
    else:
        print("FAILURE: Routing rule for 'api_in' NOT found.")

if __name__ == "__main__":
    verify()
