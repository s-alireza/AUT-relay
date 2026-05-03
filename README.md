# AUT Proxy Bridge (Easy Setup)

**Turn your university internet into a personal VPN.** This project bridges your university connection through a VPS to provide open, unrestricted internet on your phone or other devices. It automatically manages and rotates your student accounts to ensure maximum speed at all times.

---

## 📋 Before You Start (Prerequisites)

1. **An Iranian VPS**: You need a Linux VPS (Ubuntu/Debian recommended) located in Iran.
2. **Python Installed**: You must have Python (3.4 or newer) installed on your Windows PC.
    * *Note: The setup wizard needs Python to run, so install it first!*
3. **AUT Network**: Your PC must be connected to the Amirkabir University network.

---

## 🚀 Quick Start (3 Steps)

### Step 1: VPS Setup (Relay Server)

SSH into your **Iranian VPS** and follow these steps.

#### A. Download & Extract

Try the automatic download first. If it fails due to network restrictions, follow the **Manual Path**.

**Option 1: Automatic Download**

Try downloading directly from GitHub:
```bash
wget -O frp.tar.gz https://github.com/fatedier/frp/releases/download/v0.61.1/frp_0.61.1_linux_amd64.tar.gz
```

*If GitHub is blocked*, use the Iranian mirror:
```bash
wget -O frp.tar.gz https://scorpian.ir/proxy/asset/fatedier/frp/213672059
```

**Option 2: Manual Download (If automatic fails)**

1. Download the file on your PC using either:
   - [Official GitHub Link](https://github.com/fatedier/frp/releases/download/v0.61.1/frp_0.61.1_linux_amd64.tar.gz)
   - [Iranian Mirror Link](https://scorpian.ir/proxy/asset/fatedier/frp/213672059)
2. Upload it to your VPS (rename it to `frp.tar.gz`).
    * **Upload Command (Run on your local PC):**
        ```bash
        # Note: Use :~/ to upload to your home folder
        scp C:\Path\To\frp.tar.gz username@YOUR_VPS_IP:~/
        ```
    * *Or use a tool like FileZilla or WinSCP.*

**Once the file is on your VPS, run this:**

```bash
# Extract, rename folder, and cleanup
tar -xzf frp.tar.gz
mv frp_0.61.1_linux_amd64 frp
chmod +x frp/frps
rm frp.tar.gz
```

#### B. Create Configuration

Run this to create the server configuration:

```bash
echo "bindPort = 7000
kcpBindPort = 7000" > frp/frps.toml
```

#### C. Setup Auto-Start (Service)

Paste this entire block to create a background service that starts automatically on boot:

```bash
sudo tee /etc/systemd/system/frps.service > /dev/null <<EOF
[Unit]
Description=FRP Server
After=network.target

[Service]
Type=simple
ExecStart=$(pwd)/frp/frps -c $(pwd)/frp/frps.toml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

#### D. Start & Verify

Run these commands to start the server and make sure it's running correctly:

```bash
# 1. Enable and Start
sudo systemctl daemon-reload
sudo systemctl enable frps
sudo systemctl start frps

# 2. Verify Status (Should say "active (running)")
sudo systemctl status frps

# 3. Verify Port (Should show port 7000 is LISTENING)
sudo ss -tulpn | grep 7000
```

If everything looks good, you'll see `active (running)` and port `7000` in the output. **🚀 VPS is Ready!**

---

### Step 2: Local PC Setup

1. **Choose Your Release**: 
   * If you have a modern PC (Windows 10/11), open the `VLESS_Server_64bit` folder.
   * If you have an older PC (Windows 7/32-bit), open the `VLESS_Server_32bit` folder.
2. **Launch the Master Controller**: Inside your chosen folder, double-click **`Start_Server.bat`**.
3. **Web-Based Setup**: On the first run, the script will open your browser to `http://127.0.0.1:3080`. 
4. Fill in the required details:
   * **VPS IP Address**
   * **AUT Username & Password**
   * **Dashboard Credentials** (Create a username and password to secure your management panel).
5. **Unified Monitoring**: After setup, all services (Xray, FRPC, and Account Manager) will launch instantly in a single, color-coded **Master Window**. No more cluttered desktop!

---

### Step 3: Connect Your Phone

1. Install any **VLESS-compatible app** on your phone (like **Karing**, **v2rayNG**, or **v2rayN**).
2. **Copy the VLESS Link** provided at the end of the web setup and add it to your app.
3. **Connect** and enjoy open internet!

---

## 🛠 Dashboard & Management

You can securely manage your accounts and servers from any browser:

* **Local:** `http://127.0.0.1:3080`
* **Remote:** `http://YOUR_VPS_IP:8880`

### 🔄 Monitoring & Control

* **Unified Logs**: The Master Window shows live status updates with color-coded prefixes:
    * **`[XRAY]` (Blue)**: Network core events.
    * **`[FRPC]` (Red)**: Tunnel connection status.
    * **`[AUTH]/[USAGE]`**: Real-time account management logs.
* **Instant Start**: The startup script now uses dynamic port detection to initialize all services in milliseconds.
* **Auto-Rotation**: The system automatically switches between your student accounts to give you the best speed (Daily -> Weekly -> Monthly -> Free). No manual work needed!

### 🧹 How to Reset?

If you want to wipe all your data (passwords/IPs/Configs) before sharing this folder or if you want to start over, open the Web Dashboard and click the **Factory Reset** button at the bottom. This will clear everything and let you run `Start_Server.bat` anew.

---

## ❓ Troubleshooting

* **Fails to start?** Make sure Python is installed (check "Add to PATH" during installation).
* **No Internet?** Ensure your VPS firewall allows port **7000** (KCP/FRP) and **8080** (VLESS).
* **Forgot Dashboard Password?** Delete the `config` folder manually, then run `Start_Server.bat` again to recreate your setup.
