import json
import time
import re
import sys
import os
import subprocess
import threading
from datetime import datetime
import ssl

try:
    # Python 3
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import urllib.request as urllib2
    import urllib.parse as urlparse
    import http.cookiejar as cookiejar
except ImportError:
    # Python 2 (for extreme legacy cases, though we target 3.4+)
    from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
    import urllib2
    import urlparse
    import cookielib as cookiejar

import server_manager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
USAGE_STORE_PATH = os.path.join(CONFIG_DIR, "accounts_usage.json")

# ─── Configuration & Logging ────────────────────────────────────────────────

# ANSI Colors
C_RED = "\033[91m"
C_GRN = "\033[92m"
C_BLU = "\033[94m"
C_CYA = "\033[96m"
C_RST = "\033[0m"

    print("[{0}] {1}{2}{3}  {4}".format(timestamp, color, level.ljust(5), C_RST if color else "", message))
    
# Global registry for safe process cleanup
active_procs = []
proc_lock = threading.Lock()

def log_startup_summary(portal_url, accounts_count, interval):
    log("INFO", "Master Controller | {0} accounts | {1}s interval".format(accounts_count, interval), C_CYA)
    log("INFO", "Portal: {0}".format(portal_url))
    log("INFO", "Usage: https://internet.aut.ac.ir/status")

def stream_subprocess(proc, prefix, color, mgr=None):
    """Monitor subprocess output and print only critical events to keep the console clean."""
    last_noise_time = 0
    for line in iter(proc.stdout.readline, b''):
        l = line.decode('utf-8', 'ignore').strip()
        if not l: continue
        
        l_up = l.upper()
        # FILTER: Reduce noise for 'token doesn't match' during first setup
        is_token_error = "TOKEN IN LOGIN DOESN'T MATCH" in l_up or "TOKEN MISMATCH" in l_up
        
        if is_token_error:
            if mgr: mgr.set_tunnel_status("Auth Error", "Token mismatch (Check VPS config)")
            now = time.time()
            if now - last_noise_time < 60: # Only show once per minute
                continue
            last_noise_time = now
            log(prefix, "Waiting for correct VPS configuration (Token mismatch)...", color)
            continue

        if "LOGIN TO SERVER SUCCESS" in l_up:
            if mgr: mgr.set_tunnel_status("Connected")
            log(prefix, "Tunnel Established Successfully!", C_GRN)
            continue
            
        if "START PROXY SUCCESS" in l_up:
            continue # Already reported login success

        # ONLY show errors, warnings, or major status changes
        important = any(x in l_up for x in ["ERROR", "FATAL", "CRITICAL", "WARNING", "SUCCESS", "CONNECTED", "RESTART", "FAILED", "INVALID"])
        if important:
            log(prefix, l, color)
            if mgr and prefix == "FRPC" and "ERROR" in l_up:
                mgr.set_tunnel_status("Error", l)
    proc.stdout.close()
    proc.wait()
    if mgr and prefix == "FRPC":
        mgr.set_tunnel_status("Disconnected")

def persistent_process(name, cmd, cwd, prefix, color, mgr=None):
    """Keep a subprocess running forever."""
    while True:
        try:
            log("INFO", "Launching {0}...".format(name))
            proc = subprocess.Popen(
                cmd, cwd=cwd, 
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )
            with proc_lock:
                active_procs.append(proc)
            # This blocks until the process stdout is closed (process exits)
            stream_subprocess(proc, prefix, color, mgr)
            with proc_lock:
                if proc in active_procs: active_procs.remove(proc)
            log(prefix, "{0} exited. Restarting in 1s...".format(name), color)
        except Exception as e:
            log("ERROR", "Failed to launch {0}: {1}".format(name, e))
        time.sleep(1)

def load_usage_store():
    try:
        with open(USAGE_STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"accounts": {}}

def save_usage_store(store):
    try:
        with open(USAGE_STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def load_config():
    """Load settings and accounts from accounts.json"""
    try:
        with open(os.path.join(CONFIG_DIR, "accounts.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log("ERROR", "Failed to load accounts.json: {0}".format(e))
        sys.exit(1)

# ─── Portal Interaction ─────────────────────────────────────────────────────

class AUTPortal:
    """Handles login, logout, and usage scraping for internet.aut.ac.ir."""

    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self._new_session()

    def _new_session(self):
        """Create a fresh session with cookie support using standard library."""
        try:
            self.cj = cookiejar.CookieJar()
            # Bypass SSL verification for legacy environments
            try:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            except AttributeError:
                # Very old Python versions without create_default_context
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLSv1)
                ctx.verify_mode = ssl.CERT_NONE
            
            self.opener = urllib2.build_opener(
                urllib2.HTTPCookieProcessor(self.cj),
                urllib2.HTTPSHandler(context=ctx)
            )
            self.common_headers = [
                ("User-Agent", "Mozilla/5.0 (Windows NT 6.1; WOW64; Trident/7.0; rv:11.0) like Gecko"), # Win 7 IE11 style
                ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
                ("Accept-Language", "fa,en;q=0.5"),
            ]
            self.opener.addheaders = self.common_headers
        except Exception as e:
            log("ERROR", "Failed to initialize session: {0}".format(e))

    def _request(self, url, data=None, headers=None, method=None):
        """Internal helper for urllib requests."""
        if data is not None and not isinstance(data, bytes):
            data = urlparse.urlencode(data).encode("utf-8")
        
        req = urllib2.Request(url, data=data)
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        if method:
            req.get_method = lambda: method
            
        try:
            return self.opener.open(req, timeout=15)
        except Exception as e:
            raise e

    def login(self, username, password):
        """Log into the captive portal."""
        log("AUTH", "Login attempt: {0} via https://login.aut.ac.ir/login".format(username))
        payload = {
            "username": username,
            "password": password,
            "dst": "",
            "popup": "false",
            "erase-cookie": "false",
        }

        try:
            # Initial visit to get cookies
            self._request(self.base_url)
        except Exception as e:
            log("ERROR", "Cannot reach portal: {0}".format(e))
            return False

        # Use the confirmed correct login endpoint
        login_url = "https://login.aut.ac.ir/login"
        
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "{0}/login".format(self.base_url),
        }

        try:
            resp = self._request(login_url, data=payload, headers=headers)
            final_url = resp.geturl()
            html = resp.read().decode("utf-8", "ignore")

            # Check for success indicators
            if "/status" in final_url:
                log("AUTH", "Login success: {0}".format(username))
                return True

            if "GB" in html or "خروج" in html:
                log("AUTH", "Login success (content match): {0}".format(username))
                return True

            if "خطا" in html or "incorrect" in html.lower():
                log("ERROR", "Login failed (bad credentials): {0}".format(username))
                return False

            log("WARN", "Login response unknown. Final URL: {0}".format(final_url))
            return False
            
        except Exception as e:
            log("ERROR", "Login attempt failed: {0}".format(e))
            return False

    def get_usage(self):
        """Scrape the status page and extract usage data."""
        try:
            final_url, html = self._fetch_status_html()
            if html is None:
                return None

            if "login" in final_url and "/status" not in final_url:
                log("AUTH", "Session expired or redirected to login")
                return None

            return self._parse_usage(html)

        except Exception as e:
            log("ERROR", "Status check error: {0}".format(e))
            return None

    def _parse_usage(self, html):
        """Parse usage values from the status page HTML (specifically from script blocks)."""
        usage = {
            "daily_used":   None, "daily_limit":   3.0,
            "weekly_used":  None, "weekly_limit":  12.0,
            "monthly_used": None, "monthly_limit": 30.0,
            "free_used":    None, "free_limit":    120.0,
        }

        # The data is inside JavaScript: return 'X.X MB / Y GB'
        # We look for digits followed by units like GB, MB, گیگابایت, مگابایت
        pattern = r"['\"]([\d۰-۹.]+)\s*(GB|MB|گیگابایت|مگابایت)\s*(?:/|از)\s*([\d۰-۹.]+)\s*(GB|MB|گیگابایت|مگابایت)['\"]"
        matches = re.findall(pattern, html, re.IGNORECASE)

        if matches:
            for used_str, used_unit, limit_str, limit_unit in matches:
                try:
                    used = float(self._persian_to_english(used_str))
                    limit = float(self._persian_to_english(limit_str))
                    
                    # Convert MB to GB
                    if used_unit.upper() in ["MB", "مگابایت"]:
                        used = used / 1024.0
                    if limit_unit.upper() in ["MB", "مگابایت"]:
                        limit = limit / 1024.0
                    # Map by limit value (3=daily, 12=weekly, 30=monthly, 120=free)
                    if abs(limit - 3.0) < 1.1:
                        usage["daily_used"] = used
                    elif abs(limit - 12.0) < 1.5:
                        usage["weekly_used"] = used
                    elif abs(limit - 30.0) < 5.0:
                        usage["monthly_used"] = used
                    elif abs(limit - 120.0) < 15.0:
                        usage["free_used"] = used
                except Exception:
                    continue

            if usage["daily_used"] is not None or usage["weekly_used"] is not None or usage["free_used"] is not None:
                return usage

        # Debug: If parsing fails
        log("WARN", "Usage parsing failed: no matching gauges in HTML.")
        return None

    def _persian_to_english(self, text):
        persian_digits = "۰۱۲۳۴۵۶۷۸۹"
        for i, pd in enumerate(persian_digits):
            text = text.replace(pd, str(i))
        return text

    def logout(self):
        """Logout from the portal."""
        try:
            self._request("https://login.aut.ac.ir/logout")
            self._request("{0}/logout".format(self.base_url))
        except Exception:
            pass
        self._new_session()
        log("OK", "Logout requested / Session reset")

    def _fetch_status_html(self):
        url = "{0}/status".format(self.base_url)
        if url.startswith("http:"):
            url = url.replace("http:", "https:")
        resp = self._request(url)
        final_url = resp.geturl()
        html = resp.read().decode("utf-8", "ignore")
        return final_url, html

    def detect_active_account(self, accounts):
        """Try to detect which account is already logged in by scanning the status page HTML."""
        try:
            final_url, html = self._fetch_status_html()
            if html is None:
                return None, None
            if "login" in final_url and "/status" not in final_url:
                return None, None
            usage = self._parse_usage(html)
            for i, acc in enumerate(accounts):
                username = acc.get("username", "")
                if username and username in html:
                    return i, usage
            return None, usage
        except Exception:
            return None, None


class AccountRotator:
    """
    Smart account rotation with per-account, per-tier eligibility.

    Strategy (priority order — highest speed first):
      1. DAILY   — within daily, weekly, monthly AND free limits  (unlimited speed)
      2. WEEKLY  — daily blown, but weekly, monthly, free OK      (high speed)
      3. MONTHLY — daily+weekly blown, monthly and free OK        (2 Mbps)
      4. FREE    — daily+weekly+monthly blown, free still OK      (very slow)
    At every check, the rotator scans ALL accounts to find the best available one.
    Within each tier, it picks the account with the LEAST usage to spread load evenly.
    """

    PHASE_DAILY   = "daily"
    PHASE_WEEKLY  = "weekly"
    PHASE_MONTHLY = "monthly"
    PHASE_FREE    = "free"
    PHASE_ORDER   = ["daily", "weekly", "monthly", "free"]

    def __init__(self, accounts, thresholds):
        self.accounts   = accounts
        self.thresholds = thresholds
        self.current_index = 0
        self.current_phase = self.PHASE_DAILY
        # Stores the last known usage dict for each account index
        self.known_usage = {}

    @property
    def current_account(self):
        return self.accounts[self.current_index]

    def _t(self):
        """Return threshold dict."""
        return {
            self.PHASE_DAILY:   self.thresholds.get("daily_gb",   2.8),
            self.PHASE_WEEKLY:  self.thresholds.get("weekly_gb",  11.5),
            self.PHASE_MONTHLY: self.thresholds.get("monthly_gb", 29.0),
            self.PHASE_FREE:    self.thresholds.get("free_gb",    115.0),
        }

    def _eligible(self, usage, phase):
        """
        Return True if the account is eligible for `phase`.

        DAILY   needs: daily < Td  AND weekly < Tw  AND monthly < Tm  AND free < Tf
        WEEKLY  needs:                 weekly < Tw  AND monthly < Tm  AND free < Tf
        MONTHLY needs:                               monthly < Tm     AND free < Tf
        FREE    needs:                                                    free < Tf
        """
        if usage is None:
            return True   # No data yet — optimistically assume eligible

        t = self._t()
        daily   = usage.get("daily_used")   or 0
        weekly  = usage.get("weekly_used")  or 0
        monthly = usage.get("monthly_used") or 0
        free    = usage.get("free_used")    or 0

        if phase == self.PHASE_DAILY:
            return (daily   < t[self.PHASE_DAILY]   and
                    weekly  < t[self.PHASE_WEEKLY]  and
                    monthly < t[self.PHASE_MONTHLY] and
                    free    < t[self.PHASE_FREE])

        elif phase == self.PHASE_WEEKLY:
            return (weekly  < t[self.PHASE_WEEKLY]  and
                    monthly < t[self.PHASE_MONTHLY] and
                    free    < t[self.PHASE_FREE])

        elif phase == self.PHASE_MONTHLY:
            return (monthly < t[self.PHASE_MONTHLY] and
                    free    < t[self.PHASE_FREE])

        elif phase == self.PHASE_FREE:
            return free < t[self.PHASE_FREE]

        return False

    def should_rotate(self, usage):
        """
        Save the latest usage for the current account and decide if we must rotate.
        Rotation is needed if the current account is no longer eligible for its phase.
        """
        if usage is None:
            return False

        self.known_usage[self.current_index] = usage

        if not self._eligible(usage, self.current_phase):
            used = usage.get("{0}_used".format(self.current_phase), 0) or 0
            log("USAGE", "'{0}' no longer eligible for {1} phase ({2:.2f} GB used).".format(
                self.current_account["username"], self.current_phase, used))
            return True

        return False

    def rotate(self):
        """
        Find the BEST available account across all tiers and switch to it.

        Search order: DAILY > WEEKLY > MONTHLY
        Within each tier, prefer the account with the LEAST usage
        (to spread load evenly).

        Returns the new account dict, or None if all bandwidth is exhausted.
        """
        t = self._t()
        phase_keys = {
            self.PHASE_DAILY:   "daily_used",
            self.PHASE_WEEKLY:  "weekly_used",
            self.PHASE_MONTHLY: "monthly_used",
            self.PHASE_FREE:    "free_used",
        }

        for phase in self.PHASE_ORDER:
            best_idx   = None
            best_used  = float("inf")

            for i in range(len(self.accounts)):
                if i == self.current_index:
                    continue          # Skip current (already deemed ineligible)

                usage = self.known_usage.get(i)

                if self._eligible(usage, phase):
                    # Prefer the account with the least usage in this tier
                    used = (usage.get(phase_keys[phase]) or 0) if usage else 0
                    if used < best_used:
                        best_used = used
                        best_idx  = i

            if best_idx is not None:
                self.current_index = best_idx
                self.current_phase = phase
                log("OK", "AUT Switched: {0} ({1})".format(self.current_account["username"], phase))
                return self.current_account

        # No other account found — check if current account can be used in a higher phase
        usage = self.known_usage.get(self.current_index)
        for phase in self.PHASE_ORDER:
            if phase == self.current_phase:
                continue
            if self._eligible(usage, phase):
                self.current_phase = phase
                log("INFO", "AUT Escalated: {0} -> {1}".format(self.current_account["username"], phase))
                return self.current_account

        log("ERROR", "ALL accounts have exhausted ALL tiers! No bandwidth remaining.")
        return None

    def get_status_line(self, usage):
        if usage is None:
            return "Usage: Unknown"
        parts  = []
        for p in self.PHASE_ORDER:
            used   = usage.get("{0}_used".format(p), 0) or 0
            marker = "*" if p == self.current_phase else " "
            parts.append("{0}[{1:.2f}GB]".format(marker, used))
        return " | ".join(parts)

# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    # Enable ANSI escape codes for Windows 10+
    if os.name == 'nt':
        os.system('color')

    log("INFO", "=== AUT Proxy Bridge Master Controller ===", C_GRN)
    
    # 1. Kill old instances
    log("INFO", "Cleaning up old instances...")
    subprocess.call('taskkill /f /im xray.exe 2>nul', shell=True)
    subprocess.call('taskkill /f /im frpc.exe 2>nul', shell=True)

    try:
        config = load_config()
    except Exception as e:
        log("ERROR", "Config load failed: {0}".format(e))
        time.sleep(10)
        return
    accounts = config["accounts"]
    thresholds = config.get("thresholds", {"daily_gb": 2.8, "weekly_gb": 11.5, "monthly_gb": 29.0})
    interval = config.get("check_interval_seconds", 60)
    url = config.get("portal_url", "https://internet.aut.ac.ir")
    log_startup_summary(url, len(accounts), interval)

    skip_next_usage_log = False

    usage_store = load_usage_store()

    portal = AUTPortal(url)
    rotator = AccountRotator(accounts, thresholds)
    
    # Load usage store early
    usage_store = load_usage_store()
    for i, acc in enumerate(accounts):
        uname = acc.get("username", "")
        cached = usage_store.get("accounts", {}).get(uname)
        if cached:
            rotator.known_usage[i] = cached

    # 2. Start web dashboard FIRST
    mgr = server_manager.ServerManager()
    mgr.set_tunnel_status("Connecting...")
    try:
        web_thread = threading.Thread(target=server_manager.start_web_server, args=(mgr, mgr.port))
        web_thread.daemon = True
        web_thread.start()
        log("OK", "Dashboard thread launched.")
    except Exception as e:
        log("ERROR", "Dashboard failed to start: {0}".format(e))
        sys.exit(1)

    # 3. Start Xray (Persistent) - AFTER mgr has initialized config
    xray_path = os.path.join(BASE_DIR, "bin", "xray.exe")
    if os.path.exists(xray_path):
        threading.Thread(
            target=persistent_process, 
            args=("Xray core", [xray_path, "-c", os.path.join(CONFIG_DIR, "config.json")], CONFIG_DIR, "XRAY", C_BLU),
            daemon=True
        ).start()
    else:
        log("ERROR", "xray.exe not found in bin folder!")

    # 3. Start FRPC (Persistent)
    frpc_path = os.path.join(BASE_DIR, "bin", "frpc.exe")
    if os.path.exists(frpc_path):
        cfg_ext = "toml" if os.path.exists(os.path.join(CONFIG_DIR, "frpc.toml")) else "ini"
        threading.Thread(
            target=persistent_process, 
            args=("FRPC tunnel", [frpc_path, "-c", os.path.join(CONFIG_DIR, "frpc." + cfg_ext)], CONFIG_DIR, "FRPC", C_RED, mgr),
            daemon=True
        ).start()
    else:
        log("ERROR", "frpc.exe not found in bin folder!")

    def _push_status():
        """Push current state to the web dashboard."""
        cur_usage = rotator.known_usage.get(rotator.current_index, {})
        mgr.set_aut_status(
            rotator.current_account["username"],
            rotator.current_phase,
            accounts=accounts,
            usage=rotator.known_usage,
            current_idx=rotator.current_index,
            current_usage=cur_usage
        )

    def _login_and_fetch(username, password):
        """Login, immediately fetch usage, and push to dashboard."""
        nonlocal skip_next_usage_log
        ok = portal.login(username, password)
        if ok:
            usage = portal.get_usage()
            if usage:
                rotator.known_usage[rotator.current_index] = usage
                usage_store.setdefault("accounts", {})[username] = usage
                usage_store["accounts"][username]["last_seen"] = datetime.now().isoformat()
                save_usage_store(usage_store)
                log("USAGE", "{0} | {1}".format(username, rotator.get_status_line(usage)))
                skip_next_usage_log = True
        else:
            log("ERROR", "Login failed: {0}".format(username))
        _push_status()
        return ok

    detected_idx, detected_usage = portal.detect_active_account(accounts)
    if detected_idx is not None:
        rotator.current_index = detected_idx
        if detected_usage:
            rotator.known_usage[detected_idx] = detected_usage
            uname = accounts[detected_idx].get("username", "")
            if uname:
                usage_store.setdefault("accounts", {})[uname] = detected_usage
                usage_store["accounts"][uname]["last_seen"] = datetime.now().isoformat()
                save_usage_store(usage_store)
            for phase in rotator.PHASE_ORDER:
                if rotator._eligible(detected_usage, phase):
                    rotator.current_phase = phase
                    break
        log("AUTH", "Active Session: {0} ({1})".format(
            rotator.current_account["username"], rotator.current_phase))
        _push_status()

    # Initial login with immediate stats
    while True:
        acc = rotator.current_account
        if _login_and_fetch(acc["username"], acc["password"]):
            break
        if not rotator.rotate():
            log("ERROR", "No accounts available!")
            sys.exit(1)
        time.sleep(2)

    # Monitor Loop
    try:
        while True:
            # Check for forced account switch from dashboard
            forced_idx = mgr.consume_force_switch()
            # Reload accounts in case new ones were added via dashboard
            try:
                fresh = load_config()
                accounts = fresh["accounts"]
                rotator.accounts = accounts
            except Exception:
                pass

            if forced_idx >= 0 and forced_idx < len(accounts):
                log("OK", "AUT Switched (Manual): {0}".format(accounts[forced_idx].get("username", "?")))
                portal.logout()
                time.sleep(1)
                rotator.current_index = forced_idx
                rotator.current_phase = rotator.PHASE_DAILY
                acc = rotator.current_account
                _login_and_fetch(acc["username"], acc["password"])

            _push_status()

            usage = portal.get_usage()
            if usage:
                rotator.known_usage[rotator.current_index] = usage
                if skip_next_usage_log:
                    skip_next_usage_log = False
                else:
                    log("USAGE", "{0} | {1}".format(rotator.current_account["username"], rotator.get_status_line(usage)))
                _push_status()
                if rotator.should_rotate(usage):
                    next_acc = rotator.rotate()
                    if next_acc:
                        portal.logout()
                        time.sleep(3)
                        _login_and_fetch(next_acc["username"], next_acc["password"])
            else:
                log("WARN", "Usage unknown. Checking session...")
                if not portal.login(rotator.current_account["username"], rotator.current_account["password"]):
                    log("WARN", "Session dead. Re-logging...")
            
            # Interruptible sleep — wakes instantly on forced switch
            mgr.wait_for_interval(interval)
    except KeyboardInterrupt:
        log("INFO", "Shutting down all services...")
    except Exception as e:
        log("ERROR", "Master loop error: {0}".format(e))
    finally:
        log("INFO", "Cleaning up subprocesses...")
        with proc_lock:
            for p in active_procs:
                try: p.terminate()
                except: pass
        log("OK", "Master Controller stopped.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("FATAL", "Uncaught error: {0}".format(e))
        import traceback
        traceback.print_exc()
        # Keep window open on Windows so user can see the error
        if os.name == 'nt':
            try:
                raw_input("\nPress Enter to exit...")
            except NameError:
                input("\nPress Enter to exit...")

