# -*- coding: utf-8 -*-
import sys
import os

# Add core directory to path for modules
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "core"))

import json
import time
import re
import threading
from datetime import datetime
import ssl

try:
    import urllib.request as urllib2
    import urllib.parse as urlparse
    import http.cookiejar as cookiejar
except ImportError:
    import urllib2
    import urlparse
    import cookielib as cookiejar

import utils as u
from server_manager import ServerManager
from process_manager import ProcessManager
from web_server import start_server
from dashboard_handler import DashboardHandler

# ─── Configuration & Logging ────────────────────────────────────────────────

USAGE_STORE_PATH = os.path.join(u.CONFIG_DIR, "accounts_usage.json")

def load_usage_store():
    return u.read_json_file(USAGE_STORE_PATH, {"accounts": {}})

def save_usage_store(store):
    u.write_json_file(USAGE_STORE_PATH, store)

def load_config():
    """Load settings and accounts from accounts.json"""
    try:
        path = os.path.join(u.CONFIG_DIR, "accounts.json")
        return u.read_json_file(path, {"accounts": []})
    except Exception as e:
        u.log("ERROR", "Failed to load accounts.json: {0}".format(e), component="CORE")
        sys.exit(1)

# ─── Portal Interaction ─────────────────────────────────────────────────────

class AUTPortal:
    """Handles login, logout, and usage scraping for internet.aut.ac.ir."""

    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self._new_session()

    def _new_session(self):
        """Create a fresh session with cookie support."""
        try:
            self.cj = cookiejar.CookieJar()
            # Use global context if available, otherwise fallback
            ctx = u.ssl_context if u.ssl_context else None
            
            handlers = [urllib2.HTTPCookieProcessor(self.cj)]
            if ctx and hasattr(urllib2, 'HTTPSHandler'):
                handlers.append(urllib2.HTTPSHandler(context=ctx))
            
            self.opener = urllib2.build_opener(*handlers)
            self.common_headers = [
                ("User-Agent", "Mozilla/5.0 (Windows NT 6.1; WOW64; Trident/7.0; rv:11.0) like Gecko"),
                ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
                ("Accept-Language", "fa,en;q=0.5"),
            ]
            self.opener.addheaders = self.common_headers
        except Exception as e:
            u.log("ERROR", "Failed to initialize session: {0}".format(e), component="AUTH")

    def _request(self, url, data=None, headers=None, method=None):
        if data is not None and not isinstance(data, bytes):
            data = urlparse.urlencode(data).encode("utf-8")
        
        req = urllib2.Request(url, data=data)
        if headers:
            for k, v in headers.items(): req.add_header(k, v)
        if method: req.get_method = lambda: method
            
        try: return self.opener.open(req, timeout=15)
        except Exception as e: raise e

    def login(self, username, password):
        u.log("AUTH", "Login attempt: {0} via https://login.aut.ac.ir/login".format(username), component="AUTH")
        payload = { "username": username, "password": password, "dst": "", "popup": "false", "erase-cookie": "false" }

        try: self._request(self.base_url)
        except Exception as e:
            u.log("ERROR", "Cannot reach portal: {0}".format(e), component="AUTH")
            return False

        login_url = "https://login.aut.ac.ir/login"
        headers = { "Content-Type": "application/x-www-form-urlencoded", "Referer": "{0}/login".format(self.base_url) }

        try:
            resp = self._request(login_url, data=payload, headers=headers)
            final_url, html = resp.geturl(), resp.read().decode("utf-8", "ignore")

            if "/status" in final_url or "GB" in html or "خروج" in html:
                u.log("AUTH", "Login success: {0}".format(username), component="AUTH"); return True

            if "خطا" in html or "incorrect" in html.lower():
                u.log("ERROR", "Login failed (bad credentials): {0}".format(username), component="AUTH"); return False

            u.log("WARN", "Login response unknown. Final URL: {0}".format(final_url), component="AUTH"); return False
        except Exception as e:
            u.log("ERROR", "Login attempt failed: {0}".format(e), component="AUTH"); return False

    def get_usage(self):
        try:
            url = "{0}/status".format(self.base_url)
            if url.startswith("http:"): url = url.replace("http:", "https:")
            resp = self._request(url)
            final_url, html = resp.geturl(), resp.read().decode("utf-8", "ignore")
            
            if "login" in final_url and "/status" not in final_url:
                u.log("AUTH", "Session expired or redirected to login", component="AUTH"); return None
            return self._parse_usage(html)
        except Exception as e:
            u.log("ERROR", "Status check error: {0}".format(e), component="AUTH"); return None

    def _parse_usage(self, html):
        usage = {"daily_used": None, "daily_limit": 3.0, "weekly_used": None, "weekly_limit": 12.0, "monthly_used": None, "monthly_limit": 30.0, "free_used": None, "free_limit": 120.0}
        pattern = r"['\"]([\d۰-۹.]+)\s*(GB|MB|گیگابایت|مگابایت)\s*(?:/|از)\s*([\d۰-۹.]+)\s*(GB|MB|گیگابایت|مگابایت)['\"]"
        matches = re.findall(pattern, html, re.IGNORECASE)

        if matches:
            for used_str, used_unit, limit_str, limit_unit in matches:
                try:
                    used, limit = float(self._persian_to_english(used_str)), float(self._persian_to_english(limit_str))
                    if used_unit.upper() in ["MB", "مگابایت"]: used /= 1024.0
                    if limit_unit.upper() in ["MB", "مگابایت"]: limit /= 1024.0
                    if abs(limit - 3.0) < 1.1: usage["daily_used"] = used
                    elif abs(limit - 12.0) < 1.5: usage["weekly_used"] = used
                    elif abs(limit - 30.0) < 5.0: usage["monthly_used"] = used
                    elif abs(limit - 120.0) < 15.0: usage["free_used"] = used
                except Exception: continue

            if any(v is not None for v in [usage["daily_used"], usage["weekly_used"], usage["free_used"]]):
                usage["timestamp"] = time.time(); return usage
        return None

    def _persian_to_english(self, text):
        persian_digits = "۰۱۲۳۴۵۶۷۸۹"
        for i, pd in enumerate(persian_digits): text = text.replace(pd, str(i))
        return text

    def logout(self):
        try:
            self._request("https://login.aut.ac.ir/logout")
            self._request("{0}/logout".format(self.base_url))
        except: pass
        self._new_session()
        u.log("OK", "Logout requested / Session reset", component="AUTH")

class AccountRotator:
    PHASE_DAILY   = "daily"
    PHASE_WEEKLY  = "weekly"
    PHASE_MONTHLY = "monthly"
    PHASE_FREE    = "free"
    PHASE_ORDER   = ["daily", "weekly", "monthly", "free"]

    def __init__(self, accounts, thresholds):
        self.accounts, self.thresholds = accounts, thresholds
        self.current_index, self.current_phase = 0, self.PHASE_DAILY
        self.known_usage = {}
        self.failed_logins = {}

    def mark_login_failure(self):
        self.failed_logins[self.current_index] = self.failed_logins.get(self.current_index, 0) + 1

    def clear_login_failure(self):
        if self.current_index in self.failed_logins:
            del self.failed_logins[self.current_index]

    @property
    def current_account(self):
        if not self.accounts: return None
        if self.current_index >= len(self.accounts): self.current_index = 0
        return self.accounts[self.current_index]

    def _t(self):
        return { self.PHASE_DAILY: self.thresholds.get("daily_gb", 2.8), self.PHASE_WEEKLY: self.thresholds.get("weekly_gb", 11.5), self.PHASE_MONTHLY: self.thresholds.get("monthly_gb", 29.0), self.PHASE_FREE: self.thresholds.get("free_gb", 115.0) }

    def _eligible(self, usage, phase, index):
        if self.failed_logins.get(index, 0) >= 3: return False
        if usage is None: return True
        t = self._t()
        daily, weekly, monthly, free = usage.get("daily_used") or 0, usage.get("weekly_used") or 0, usage.get("monthly_used") or 0, usage.get("free_used") or 0
        
        ts = usage.get("timestamp")
        if ts:
            try:
                if datetime.fromtimestamp(ts).date() < datetime.now().date(): daily = 0
            except: pass

        if phase == self.PHASE_DAILY: return daily < t[self.PHASE_DAILY] and weekly < t[self.PHASE_WEEKLY] and monthly < t[self.PHASE_MONTHLY] and free < t[self.PHASE_FREE]
        elif phase == self.PHASE_WEEKLY: return weekly < t[self.PHASE_WEEKLY] and monthly < t[self.PHASE_MONTHLY] and free < t[self.PHASE_FREE]
        elif phase == self.PHASE_MONTHLY: return monthly < t[self.PHASE_MONTHLY] and free < t[self.PHASE_FREE]
        elif phase == self.PHASE_FREE: return free < t[self.PHASE_FREE]
        return False

    def should_rotate(self, usage):
        if usage is None: return False
        old_usage = self.known_usage.get(self.current_index)
        self.known_usage[self.current_index] = usage

        if not self._eligible(usage, self.current_phase, self.current_index): return True
        for phase in self.PHASE_ORDER:
            if phase == self.current_phase: break
            for i in range(len(self.accounts)):
                if i != self.current_index and self._eligible(self.known_usage.get(i), phase, i): return True

        if len(self.accounts) > 1:
            pk = "{0}_used".format(self.current_phase)
            new_v = usage.get(pk, 0) or 0
            old_v = (old_usage.get(pk, 0) or 0) if old_usage else new_v
            session_gb = self.thresholds.get("session_gb", 1.0)
            if session_gb > 0 and int(new_v / session_gb) > int(old_v / session_gb): return True
        return False

    def rotate(self, prefer_different=False):
        phase_keys = { self.PHASE_DAILY: "daily_used", self.PHASE_WEEKLY: "weekly_used", self.PHASE_MONTHLY: "monthly_used", self.PHASE_FREE: "free_used" }
        for phase in self.PHASE_ORDER:
            eligible = [i for i in range(len(self.accounts)) if self._eligible(self.known_usage.get(i), phase, i)]
            if prefer_different:
                eligible = [i for i in eligible if i != self.current_index]
            if not eligible: continue
            
            best_idx, min_usage = None, float("inf")
            for i in eligible:
                usage = self.known_usage.get(i)
                used = (usage.get(phase_keys[phase]) or 0) if usage else 0
                if used < min_usage: min_usage, best_idx = used, i

            if best_idx is not None:
                is_same = (best_idx == self.current_index)
                self.current_index, self.current_phase = best_idx, phase
                if not is_same: u.log("OK", "AUT Switched (Load Balance): {0} ({1})".format(self.current_account["username"], phase), component="AUTH")
                return self.current_account
                
        if prefer_different and len(self.accounts) > 1:
            self.current_index = (self.current_index + 1) % len(self.accounts)
            self.current_phase = self.PHASE_DAILY
            u.log("WARN", "All accounts exhausted or blacklisted. Force rotating to next: {0}".format(self.current_account["username"]), component="AUTH")
            return self.current_account
            
        return None

    def get_status_line(self, usage):
        if usage is None: return "Usage: Unknown"
        return " | ".join(["{0}[{1:.2f}GB]".format("*" if p == self.current_phase else " ", usage.get("{0}_used".format(p), 0) or 0) for p in self.PHASE_ORDER])

# ─── Main Logic ────────────────────────────────────────────────────────────

def main():
    if os.name == 'nt': os.system('color')
    u.log("INFO", "=== AUT Proxy Bridge Master Controller ===", component="CORE")
    
    # Initialize Core Managers
    proc_mgr = ProcessManager()
    mgr = ServerManager(process_manager=proc_mgr)
    proc_mgr.mgr = mgr # Circular ref for status updates
    
    config = load_config()
    accounts = config["accounts"]
    thresholds = config.get("thresholds", {"daily_gb": 2.8, "weekly_gb": 11.5, "monthly_gb": 29.0})
    interval = config.get("check_interval_seconds", 60)
    url = config.get("portal_url", "https://internet.aut.ac.ir")
    
    u.log("INFO", "Master Controller | {0} accounts | {1}s interval".format(len(accounts), interval), component="CORE")

    portal = AUTPortal(url)
    rotator = AccountRotator(accounts, thresholds)
    portal.logout()
    
    usage_store = load_usage_store()
    for i, acc in enumerate(accounts):
        cached = usage_store.get("accounts", {}).get(acc.get("username", ""))
        if cached: rotator.known_usage[i] = cached

    def _push_status():
        cur = rotator.known_usage.get(rotator.current_index, {})
        mgr.set_aut_status(rotator.current_account["username"] if accounts else "None", rotator.current_phase if accounts else "N/A", accounts=accounts, usage=rotator.known_usage, current_idx=rotator.current_index, current_usage=cur)

    def _login_and_fetch(acc):
        for attempt in range(3):
            try:
                if portal.login(acc["username"], acc["password"]):
                    rotator.clear_login_failure()
                    usage = portal.get_usage()
                    if usage:
                        rotator.known_usage[rotator.current_index] = usage
                        usage_store.setdefault("accounts", {})[acc["username"]] = usage
                        usage_store["accounts"][acc["username"]]["last_seen"] = datetime.now().isoformat()
                        save_usage_store(usage_store)
                        u.log("USAGE", "{0} | {1}".format(acc["username"], rotator.get_status_line(usage)), component="AUTH")
                        _push_status(); return True
                time.sleep(2)
            except: time.sleep(2)
        rotator.mark_login_failure()
        _push_status(); return False

    # 1. ACHIEVE INTERNET
    if not accounts: u.log("WARN", "No accounts. Waiting for dashboard...", component="AUTH")
    else:
        u.log("INFO", "Achieving initial internet connection...", component="CORE")
        while not _login_and_fetch(rotator.current_account):
            if not rotator.rotate(): u.log("ERROR", "No accounts ready! Retrying...", component="AUTH"); time.sleep(5)
            else: time.sleep(2)
        u.log("OK", "Internet connection established.", component="CORE")

    # 2. START INFRASTRUCTURE
    mgr.start_background_tasks()
    start_server(DashboardHandler, mgr, mgr.port, "Dashboard")

    x_path, f_path = os.path.join(u.BIN_DIR, "xray.exe"), os.path.join(u.BIN_DIR, "frpc.exe")
    if os.path.exists(x_path):
        proc_mgr.start_persistent_process("XRAY", [x_path, "-c", os.path.join(u.CONFIG_DIR, "config.json")], u.BIN_DIR, "XRAY", u.C_BLU)
    if os.path.exists(f_path):
        ext = "ini" # 32-bit default
        proc_mgr.start_persistent_process("FRPC", [f_path, "-c", os.path.join(u.CONFIG_DIR, "frpc." + ext)], u.CONFIG_DIR, "FRPC", u.C_RED)

    # 3. MONITOR LOOP
    try:
        while True:
            try:
                forced = mgr.consume_force_switch()
                try:
                    fresh = load_config()
                    accounts = fresh["accounts"]
                    rotator.accounts = accounts
                    
                    # Dynamically update thresholds from manager settings
                    current_thresholds = mgr.settings.get("thresholds")
                    if current_thresholds:
                        rotator.thresholds = current_thresholds
                except: pass

                if 0 <= forced < len(accounts):
                    u.log("OK", "AUT Switched (Manual): {0}".format(accounts[forced]["username"]), component="AUTH")
                    portal.logout(); time.sleep(1)
                    rotator.current_index, rotator.current_phase = forced, rotator.PHASE_DAILY
                    _login_and_fetch(rotator.current_account)

                _push_status()
                if accounts:
                    usage = None
                    for _ in range(3):
                        try:
                            usage = portal.get_usage()
                            if usage: break
                        except: pass
                        time.sleep(2)

                    if usage:
                        rotator.known_usage[rotator.current_index] = usage
                        u.log("USAGE", "{0} | {1}".format(rotator.current_account["username"], rotator.get_status_line(usage)), component="AUTH")
                        _push_status()
                        if rotator.should_rotate(usage):
                            next_a = rotator.rotate(prefer_different=True)
                            if next_a: portal.logout(); time.sleep(3); _login_and_fetch(next_a)
                    else:
                        if not portal.login(rotator.current_account["username"], rotator.current_account["password"]):
                            rotator.mark_login_failure()
                            u.log("WARN", "Login failed. Session dead or bad credentials. Rotating...", component="AUTH")
                            next_a = rotator.rotate(prefer_different=True)
                            if next_a: portal.logout(); time.sleep(3); _login_and_fetch(next_a)
                        else:
                            rotator.clear_login_failure()
                
                mgr.wait_for_interval(interval)
            except Exception as e:
                u.log("ERROR", "Unexpected error in monitor loop: {0}".format(e), component="CORE")
                time.sleep(5)
    except KeyboardInterrupt: u.log("INFO", "Shutting down...", component="CORE")
    finally:
        proc_mgr.stop_all()
        u.log("OK", "Stopped.", component="CORE")

if __name__ == "__main__":
    try: main()
    except Exception as e:
        u.log("FATAL", "Uncaught: {0}".format(e))
        import traceback; traceback.print_exc()
        if os.name == 'nt': input("\nPress Enter to exit...")
