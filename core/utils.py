# -*- coding: utf-8 -*-
import json
import os
import uuid
import random
import string
import ssl
from datetime import datetime

# ─── Global SSL Monkeypatch ──────────────────────────────────────────────────
try:
    if hasattr(ssl, '_create_unverified_context'):
        ssl._create_default_https_context = ssl._create_unverified_context
        ssl_context = ssl._create_unverified_context()
    else:
        ssl_context = None
except:
    ssl_context = None

# ─── Constants ───────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
UI_DIR = os.path.join(BASE_DIR, "ui")
BIN_DIR = os.path.join(BASE_DIR, "bin")

LOG_BUFFER = []
_MAX_LOG_SIZE = 100

def set_max_log_size(size):
    global _MAX_LOG_SIZE
    _MAX_LOG_SIZE = int(size)

# ANSI Colors
C_RED = "\033[91m"
C_GRN = "\033[92m"
C_BLU = "\033[94m"
C_CYA = "\033[96m"
C_YLW = "\033[93m"
C_RST = "\033[0m"

# ─── File I/O ───────────────────────────────────────────────────────────────

try:
    from html import escape as html_escape
except ImportError:
    try:
        import cgi
        def html_escape(s): return cgi.escape(s, quote=True)
    except:
        def html_escape(s): return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")

def read_json_file(path, default=None):
    try:
        if not os.path.exists(path):
            return default if default is not None else {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}

def write_json_file(path, payload):
    """Simple JSON write."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

def write_json_atomic(path, data):
    """Write JSON atomically using a temporary file to prevent corruption."""
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(path):
            os.remove(path)
        os.rename(tmp_path, path)
        return True
    except Exception as e:
        log("ERROR", "Atomic write failed for {0}: {1}".format(path, e))
        return False

# ─── Identity & Tokens ──────────────────────────────────────────────────────

def generate_token(length=32):
    alphabet = string.ascii_letters + string.digits
    try:
        rng = random.SystemRandom()
    except Exception:
        rng = random
    return "".join(rng.choice(alphabet) for _ in range(length))

def generate_uuid():
    return str(uuid.uuid4())

def to_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default

def utc_now_iso():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

# ─── Logging ────────────────────────────────────────────────────────────────

def log(level, msg, color="", component=None):
    """
    Enhanced logging with console colors, memory buffer, and persistent file logging.
    """
    LEVEL_COLORS = {
        "ERROR": C_RED,
        "WARN": C_YLW,
        "OK": C_GRN,
        "SUCCESS": C_GRN,
        "INFO": C_CYA,
        "DEBUG": C_BLU,
        "AUTH": C_YLW,
        "USAGE": C_GRN,
        "VPS": C_CYA,
        "FRPC": C_BLU
    }
    
    if not color:
        color = LEVEL_COLORS.get(level.upper(), "")

    now = datetime.now()
    ts = now.strftime("%H:%M:%S")
    ts_full = now.strftime("%Y-%m-%d %H:%M:%S")
    
    comp_str = "[{0}] ".format(component) if component else ""
    level_str = level.ljust(5)
    
    # Console output with colors
    if color:
        console_line = "[{0}] {1}{2}{3}  {4}{5}".format(ts, color, level_str, C_RST, comp_str, msg)
    else:
        console_line = "[{0}] {1}  {2}{3}".format(ts, level_str, comp_str, msg)
    print(console_line)
    
    # Memory Buffer (for web dashboard)
    # We strip ANSI colors if any were manually passed in msg
    plain_line = "[{0}] {1}  {2}{3}".format(ts, level_str, comp_str, msg)
    LOG_BUFFER.append(plain_line)
    if len(LOG_BUFFER) > _MAX_LOG_SIZE:
        LOG_BUFFER.pop(0)
        
    # Persistent File Logging
    try:
        log_path = os.path.join(CONFIG_DIR, "server.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("[{0}] {1}  {2}{3}\n".format(ts_full, level_str, comp_str, msg))
    except:
        pass

def get_logs():
    return LOG_BUFFER

def clear_logs():
    LOG_BUFFER.clear()

_sys_cache = {"last_update": 0, "stats": None}

def get_system_stats():
    """Get CPU and RAM stats optimized for legacy Windows (no wmic, slow systeminfo)."""
    import subprocess
    import platform
    import re
    import time
    
    now = time.time()
    # Cache dynamic stats for 10 seconds, static stats (OS, Total RAM) indefinitely
    if _sys_cache["stats"] and (now - _sys_cache["last_update"]) < 10:
        return _sys_cache["stats"]
    
    stats = _sys_cache["stats"] or {
        "cpu": 0, "ram_total": 0, "ram_free": 0, "ram_used_pct": 0, 
        "os": platform.system() + " " + platform.release()
    }
    
    if platform.system() != "Windows":
        return stats
    
    try:
        # 1. Get Static Info (Only once)
        if stats["ram_total"] == 0:
            # Silence systeminfo with stderr redirection
            si_out = subprocess.check_output("systeminfo", shell=True, stderr=subprocess.STDOUT).decode("utf-8", "ignore")
            
            total_m = re.search(r"Total Physical Memory:\s+([0-9,.\s]+)MB", si_out)
            if total_m:
                t_mb = int(re.sub(r"[^0-9]", "", total_m.group(1)))
                stats["ram_total"] = t_mb * 1024 * 1024
            
            os_name = re.search(r"OS Name:\s+(.+)", si_out)
            if os_name:
                stats["os"] = os_name.group(1).strip()

        # 2. Get Dynamic Stats (via typeperf - fast)
        cmd = 'typeperf "\\Processor(_Total)\\% Processor Time" "\\Memory\\Available MBytes" -sc 1'
        tp_out = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode("utf-8", "ignore")
        
        lines = [l for l in tp_out.splitlines() if l.startswith('"')]
        if len(lines) >= 2:
            # Expected format: "datetime","cpu_val","mem_val"
            # We use a more robust split to handle potential commas in numbers
            vals = re.findall(r'"([^"]+)"', lines[1])
            if len(vals) >= 3:
                try:
                    cpu_raw = float(vals[1])
                    # typeperf sometimes returns -1.0 or very large numbers if the counter isn't ready
                    if 0 <= cpu_raw <= 100:
                        stats["cpu"] = int(cpu_raw)
                    
                    free_mb = int(float(vals[2]))
                    if free_mb >= 0:
                        stats["ram_free"] = free_mb * 1024 * 1024
                        if stats["ram_total"] > 0:
                            total_mb = stats["ram_total"] / (1024*1024)
                            used_mb = max(0, total_mb - free_mb)
                            stats["ram_used_pct"] = round((used_mb / float(total_mb)) * 100, 1)
                except (ValueError, TypeError):
                    pass

        _sys_cache["stats"] = stats
        _sys_cache["last_update"] = now
    except:
        pass
        
    return stats
