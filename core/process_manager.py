# -*- coding: utf-8 -*-
import subprocess
import threading
import time
import os
from utils import log, C_GRN, C_RED, C_BLU

class ProcessManager:
    def __init__(self, mgr=None):
        self.mgr = mgr
        self.procs = {}  # {name: Popen}
        self.restart_events = {} # {name: Event}
        self.lock = threading.Lock()
        self.active = True

    def start_persistent_process(self, name, cmd, cwd, prefix, color):
        """Keep a process running in a background thread."""
        event = threading.Event()
        with self.lock:
            self.restart_events[name] = event
            
        t = threading.Thread(
            target=self._proc_loop, 
            args=(name, cmd, cwd, prefix, color, event)
        )
        t.daemon = True
        t.start()

    def _proc_loop(self, name, cmd, cwd, prefix, color, restart_event):
        while self.active:
            try:
                log("INFO", "Launching {0}...".format(name), component=prefix)
                proc = subprocess.Popen(
                    cmd, cwd=cwd, 
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT
                )
                
                with self.lock:
                    self.procs[name] = proc

                # Monitor thread for output
                monitor_thread = threading.Thread(
                    target=self._stream_output, 
                    args=(proc, prefix, color)
                )
                monitor_thread.daemon = True
                monitor_thread.start()

                # Wait for process to exit OR for a restart event
                while proc.poll() is None:
                    if restart_event.is_set():
                        log("INFO", "Restart event triggered for {0}".format(name), component=prefix)
                        try:
                            # Use taskkill on Windows to be sure
                            os.system('taskkill /f /t /pid {0} >nul 2>&1'.format(proc.pid))
                        except:
                            proc.terminate()
                        restart_event.clear()
                        break
                    time.sleep(0.5)

                with self.lock:
                    if self.procs.get(name) == proc:
                        del self.procs[name]

                if self.active:
                    log("WARN", "{0} exited. Restarting in 1s...".format(name), component=prefix)
                    time.sleep(1)
            except Exception as e:
                log("ERROR", "Process loop for {0} failed: {1}".format(name, e), component="PM")
                time.sleep(2)

    def _stream_output(self, proc, prefix, color):
        last_noise_time = 0
        for line in iter(proc.stdout.readline, b''):
            l = line.decode('utf-8', 'ignore').strip()
            if not l: continue
            
            l_up = l.upper()
            
            # Subprocess-specific logic (inherited from account_manager.py)
            if "TOKEN IN LOGIN DOESN'T MATCH" in l_up or "TOKEN MISMATCH" in l_up:
                if self.mgr: self.mgr.set_tunnel_status("Auth Error", "Token mismatch (Check VPS config)")
                now = time.time()
                if now - last_noise_time < 60: continue
                last_noise_time = now
                log(prefix, "Waiting for correct VPS configuration (Token mismatch)...", color)
                continue

            if any(x in l.lower() for x in ["deprecated", "not recommended", "migrate to"]):
                continue

            if prefix == "XRAY" and any(x in l.lower() for x in ["proxy/http: failed to read response", "failed to process outbound traffic", "app/proxyman/inbound: connection ends"]):
                continue

            if "LOGIN TO SERVER SUCCESS" in l_up:
                if self.mgr: self.mgr.set_tunnel_status("Connected")
                log(prefix, "Tunnel Established Successfully!", C_GRN)
                continue

            important = any(x in l_up for x in ["ERROR", "FATAL", "CRITICAL", "WARNING", "SUCCESS", "CONNECTED", "RESTART", "FAILED", "INVALID"])
            if important:
                if prefix in ["FRPC", "XRAY"]:
                    log("INFO", l, component=prefix)
                else:
                    log(prefix, l, color)

                if self.mgr and prefix == "FRPC" and "ERROR" in l_up:
                    benign = ["ALREADY EXISTS", "ACTIVELY REFUSED", "CONNECTION REFUSED"]
                    if not any(x in l_up for x in benign):
                        self.mgr.set_tunnel_status("Error", l)
        
        proc.stdout.close()
        if self.mgr and prefix == "FRPC" and not self.active:
             self.mgr.set_tunnel_status("Disconnected")

    def trigger_restart(self, name):
        with self.lock:
            event = self.restart_events.get(name)
            if event:
                event.set()
                return True
        return False

    def stop_all(self):
        self.active = False
        with self.lock:
            for name, proc in self.procs.items():
                try:
                    os.system('taskkill /f /t /pid {0} >nul 2>&1'.format(proc.pid))
                except:
                    proc.terminate()
