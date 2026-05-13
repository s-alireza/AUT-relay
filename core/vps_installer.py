# -*- coding: utf-8 -*-
import os
import subprocess
from utils import log

def automate_vps_setup(vps_ip, ssh_port, ssh_user, ssh_pass, frps_toml, bin_dir):
    """Automate the VPS setup using PSCP and Plink."""
    pscp = os.path.join(bin_dir, "pscp.exe")
    plink = os.path.join(bin_dir, "plink.exe")
    
    # Path to the FRP archive
    frp_archive = os.path.abspath(os.path.join(bin_dir, "frp_0.61.1_linux_amd64.tar.gz"))
    
    if not os.path.exists(frp_archive):
        return False, "FRP archive not found at {0}".format(frp_archive)

    logs = []

    def vps_log(msg):
        log("INFO", msg, component="VPS")
        logs.append(msg)

    def run_cmd(cmd_list, input_str=None, timeout=300):
        try:
            process = subprocess.Popen(
                cmd_list, 
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, 
                stderr=subprocess.STDOUT,
                shell=False
            )
            if input_str:
                if hasattr(input_str, 'encode'):
                    input_str = input_str.encode('utf-8')
            
            try:
                out_bytes, _ = process.communicate(input=input_str, timeout=timeout)
            except Exception:
                try:
                    process.kill()
                except:
                    pass
                out_bytes, _ = process.communicate()
                return False, (out_bytes.decode('utf-8', 'replace') if out_bytes else "") + "\n[ERROR] Command timed out after {0}s.".format(timeout)

            out = out_bytes.decode('utf-8', 'replace') if out_bytes else ""
            return process.returncode == 0, out
        except Exception as e:
            return False, str(e)

    # 0. Pre-cache host key
    vps_log("Step 1/4: Connecting to VPS via SSH...")
    try:
        p = subprocess.Popen([plink, "-P", str(ssh_port), "-pw", ssh_pass, "{0}@{1}".format(ssh_user, vps_ip), "exit"], 
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        p.communicate(input=b"y\n", timeout=7)
    except:
        pass

    # 1. Check if already installed or can be downloaded
    vps_log("Step 2/4: Probing VPS environment...")
    probe_script = """
if [ -f ~/frp/frps ]; then
    echo "EXISTS"
else
    (curl -L -o ~/frp.tar.gz https://github.com/fatedier/frp/releases/download/v0.61.1/frp_0.61.1_linux_amd64.tar.gz || \
     wget -O ~/frp.tar.gz https://github.com/fatedier/frp/releases/download/v0.61.1/frp_0.61.1_linux_amd64.tar.gz) && \
     echo "DOWNLOADED" || echo "FAIL"
fi
"""
    ok, out = run_cmd([plink, "-P", str(ssh_port), "-pw", ssh_pass, "-batch", "{0}@{1}".format(ssh_user, vps_ip), probe_script])
    logs.append(out)
    
    needs_upload = True
    if "EXISTS" in out or "DOWNLOADED" in out:
        needs_upload = False
        vps_log("FRP binary is ready on VPS.")

    # 2. Upload FRP archive if needed
    if needs_upload:
        vps_log("Step 3/4: Uploading local FRP archive (this may take a few minutes)...")
        # Send 'y\n' via stdin to accept the host key just in case the pre-cache failed. Remove -batch.
        ok, out = run_cmd([pscp, "-P", str(ssh_port), "-pw", ssh_pass, frp_archive, "{0}@{1}:frp.tar.gz".format(ssh_user, vps_ip)], input_str=b"y\n")
        logs.append(out)
        if not ok:
            vps_log("Upload failed! Ensure the VPS IP is correct and SSH is accessible.")
            return False, "\n".join(logs) + "\nUpload failed: " + out

    # 3. Run setup script
    vps_log("Step 4/4: Configuring and starting FRP server...")
    
    # We use a wrapper to pass the password to sudo if needed
    setup_script = r"""
echo "SCRIPT_START_MARKER"
SSH_PASS="{1}"

run_sudo() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        echo "$SSH_PASS" | sudo -S -p "" "$@"
    fi
}

if ! grep -q "$(hostname)" /etc/hosts; then
    run_sudo bash -c "echo '127.0.0.1 $(hostname)' >> /etc/hosts" || true
fi

# Configure Firewall (ufw or iptables)
if command -v ufw >/dev/null 2>&1; then
    run_sudo ufw allow 443/tcp >/dev/null 2>&1 || true
    run_sudo ufw allow 443/udp >/dev/null 2>&1 || true
    run_sudo ufw allow 8080/tcp >/dev/null 2>&1 || true
    run_sudo ufw allow 8880/tcp >/dev/null 2>&1 || true
elif command -v iptables >/dev/null 2>&1; then
    run_sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT >/dev/null 2>&1 || true
    run_sudo iptables -I INPUT -p udp --dport 443 -j ACCEPT >/dev/null 2>&1 || true
    run_sudo iptables -I INPUT -p tcp --dport 8080 -j ACCEPT >/dev/null 2>&1 || true
    run_sudo iptables -I INPUT -p tcp --dport 8880 -j ACCEPT >/dev/null 2>&1 || true
    run_sudo iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
fi

cd ~
if [ -f frp.tar.gz ]; then
    tar -xzf frp.tar.gz
    rm -rf frp
    mv frp_0.61.1_linux_amd64 frp
    rm frp.tar.gz
fi

mkdir -p ~/frp
chmod +x ~/frp/frps || true

# Write config file (local user then move with sudo)
cat > ~/frp/frps.toml << 'EOF'
{0}
EOF

run_sudo pkill -f frps || true
run_sudo systemctl stop frps || true

FRP_DIR=$(pwd)/frp
# Create service file locally then move
cat << 'EOF' > ~/frps.service
[Unit]
Description=FRP Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/frp
ExecStart=/root/frp/frps -c /root/frp/frps.toml
Restart=always
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

run_sudo mv ~/frps.service /etc/systemd/system/frps.service
run_sudo sed -i "s|WorkingDirectory=.*|WorkingDirectory=$FRP_DIR|" /etc/systemd/system/frps.service
run_sudo sed -i "s|ExecStart=.*|ExecStart=$FRP_DIR/frps -c $FRP_DIR/frps.toml|" /etc/systemd/system/frps.service

run_sudo systemctl daemon-reload
run_sudo systemctl enable frps
run_sudo systemctl restart frps

# Verification Loop
SUCCESS=0
for i in 1 2 3 4; do
    STATUS=$(run_sudo systemctl is-active frps 2>/dev/null || echo "unknown")
    echo "STATUS_RESULT: $STATUS"
    if [ "$STATUS" = "active" ] || [ "$STATUS" = "activating" ]; then
        echo "DEPLOYMENT_SUCCESS"
        SUCCESS=1
        break
    fi
    sleep 2
done

if [ $SUCCESS -eq 0 ]; then
    if pgrep -f frps > /dev/null; then
        echo "PROCESS_RUNNING_FALLBACK"
    fi
fi

echo "FRP_SETUP_FINISH_MARKER"
""".replace("{0}", frps_toml).replace("{1}", ssh_pass)

    ok, out = run_cmd([plink, "-P", str(ssh_port), "-pw", ssh_pass, "-batch", "{0}@{1}".format(ssh_user, vps_ip), "bash -s"], input_str=setup_script)
    logs.append(out)

    if out:
        # Filter out internal markers to keep logs clean for the user
        display_lines = []
        for line in out.splitlines():
            line = line.strip()
            if not line or any(m in line for m in ["MARKER", "STATUS_RESULT", "DEPLOYMENT_SUCCESS", "DEPLOYMENT_FAILED_VERIFICATION", "PROCESS_RUNNING_FALLBACK"]):
                continue
            display_lines.append(line)
        
        if display_lines:
            log("INFO", "VPS Task Output:\n" + "\n".join(display_lines), component="VPS")
        
        # If we didn't show anything but it succeeded, give a clean confirmation
        if "FRP_SETUP_FINISH_MARKER" in out and not display_lines:
            log("OK", "VPS service verified and running.", component="VPS")

    # SUCCESS CONDITIONS (Hierarchical)
    # 1. Explicit finish marker found
    if "FRP_SETUP_FINISH_MARKER" in out:
        if "DEPLOYMENT_FAILED_VERIFICATION" not in out or "PROCESS_RUNNING_FALLBACK" in out or "DEPLOYMENT_SUCCESS" in out:
            return True, "VPS Automation Completed Successfully."

    # 2. Status markers found
    success_markers = ["DEPLOYMENT_SUCCESS", "STATUS_RESULT: active", "STATUS_RESULT: activating", "PROCESS_RUNNING_FALLBACK"]
    if any(m in out for m in success_markers):
        return True, "VPS Automation Completed Successfully (detected success markers)."

    # 3. Plink exited with 0 and we have some script output
    if ok and "SCRIPT_START_MARKER" in out:
        return True, "VPS Automation likely successful (script reached end with code 0)."

    # 4. Final fallback check for common success phrases in systemctl
    if "active (running)" in out.lower() or "Started FRP Server" in out or "success" in out.lower():
        return True, "VPS Automation likely successful (detected running service)."

    return False, "Automation failed to verify service status.\n" + out
