[English](README.md) | [فارسی](README_fa.md)

# AUT Relay

Turn your university internet into a personal VPN. This project bridges your university connection through a VPS to provide open, unrestricted internet on your phone or other devices. It automatically manages and rotates your student accounts to ensure maximum speed at all times.

> 💡 **The Idea:** By simply leaving a Windows laptop or PC running at the university connected to the local network, you can access the university's unrestricted internet from anywhere. This is a service that the university's IT department should have provided (like Beheshti, IUST, etc.), but since they haven't, you can set it up yourself! This is especially useful for dorm students who cannot physically come to Tehran just to use the university internet.
>
> All you need is an Iranian VPS (which is much more cost-effective than buying a VPN or even Pro internet packages in some cases). You can even team up with friends to leave one computer running and add multiple student accounts (including those from seniors or alumni) to split the costs. Also, make sure to test the V2ray configs you previously used to connect and add them to your client apps. Good luck, future versions are on the way!
---

## 📥 Download

Get the latest version for your system from the **[Releases Page](https://github.com/s-alireza/AUT-relay/releases/latest)**:

* 🚀 **[Download 64-bit (Windows 10/11)](https://github.com/s-alireza/AUT-relay/releases/download/v1.1.0/AUT-Relay-v1.1.0-x64.zip)**
* 📦 **[Download 32-bit (Windows 7/Old PC)](https://github.com/s-alireza/AUT-relay/releases/download/v1.1.0/AUT-Relay-v1.1.0-x32.zip)**


---

## 📋 Before You Start (Prerequisites)

1. **An Iranian VPS**: You need a Linux VPS (Ubuntu) located in Iran (You can purchase from ArvanCloud).
2. **Python Installed**: You must have Python (3.4 or newer) installed on your Windows PC.
    * *Note: The setup wizard needs Python to run, so install it first!*
3. **AUT Network**: Your PC must be connected to the Amirkabir University network.

---

## 🚀 Quick Start (3 Steps)

### Step 1: Local PC Configuration

1. **Prerequisites**: Ensure you have your **VPS IP Address** and **Python** installed.
2. **Choose Your Release**:
   * If you have a modern PC (Windows 10/11), open the `VLESS_Server_64bit` folder.
   * If you have an older PC (Windows 7/32-bit), open the `VLESS_Server_32bit` folder.
3. **Launch the Setup**: Inside your chosen folder, double-click **`Start_Server.bat`**.
4. **Web-Based Wizard**: The script will automatically launch your browser into our premium setup wizard at `http://127.0.0.1:3080`.
5. **Generate Config & Automate**: 
   * Fill in your VPS IP, AUT credentials, and dashboard password.
   * ✨ **NEW:** Check the **"Automate VPS Setup (SSH)"** box, enter your VPS root password, and the wizard will install everything on your VPS automatically!
6. **Finish**: If you chose the automated setup, wait for the success message. If you chose manual setup, the wizard will display a **VPS Configuration** block for the next step.

---

### Step 2: VPS Server Setup (Skip if you used Automation)

*If you checked the "Automate VPS Setup" box in Step 1, the bridge is already installed on your server! You can skip directly to **Step 3**.*

If you prefer to configure your server manually, SSH into your **Iranian VPS** and follow these steps:

#### A. Download & Extract FRP

Choose **one** of these methods to download FRP on your VPS:

> **Option 1: GitHub (Standard)**
>
> ```bash
> wget -O frp.tar.gz https://github.com/fatedier/frp/releases/download/v0.61.1/frp_0.61.1_linux_amd64.tar.gz
> ```

> **Option 2: Iranian Mirror (If GitHub is slow/blocked)**
>
> ```bash
> wget -O frp.tar.gz https://scorpian.ir/proxy/asset/fatedier/frp/213672059
> ```

> **Option 3: Manual Installation (WinSCP/SFTP)**
> If `wget` fails, download `frp_0.61.1_linux_amd64.tar.gz` to your PC and upload it to your server using **WinSCP**, **FileZilla**, or **Termius**.

**Extract the files:**

```bash
tar -xzf frp.tar.gz && mv frp_0.61.1_linux_amd64 frp && chmod +x frp/frps
```

#### B. Apply Configuration

Run this command, **paste the VPS Configuration block** from the web setup, and then **press `Ctrl+D`** to save and exit:

```bash
cat > frp/frps.toml
```

#### C. Setup Auto-Start (Service)

Paste this entire block to ensure the tunnel starts automatically and has permission to use port 443:

```bash
# Set permissions for port 443
sudo setcap 'cap_net_bind_service=+ep' $(pwd)/frp/frps

# Create the service file
CUR_DIR=$(pwd)
sudo tee /etc/systemd/system/frps.service > /dev/null <<EOF
[Unit]
Description=FRP Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$CUR_DIR/frp
ExecStart=$CUR_DIR/frp/frps -c $CUR_DIR/frp/frps.toml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload && sudo systemctl enable frps && sudo systemctl restart frps
```

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
* **No Internet?** Ensure your VPS provider's **External Firewall Dashboard** (Security Group) allows port **443** (FRP/TLS) and **8880** (Remote Dashboard).
* **Connection Error?** Check if another service (like Nginx) is already using port 443 on your VPS.
* **Forgot Dashboard Password?** Delete the `config` folder manually, then run `Start_Server.bat` again to recreate your setup.
