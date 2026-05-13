[English](README.md) | [فارسی](README_fa.md)

# AUT Relay

AUT Relay is an open-source proxy solution that bridges your university network connection to an external VPS, providing unrestricted internet access. It features a modern Web UI, multi-user management, and an intelligent load balancer that dynamically rotates student accounts to optimize bandwidth usage and maintain maximum connection speed.

> 💡 **The Idea:** By leaving a laptop or PC running at the university connected to the local network, you can access the university's unrestricted internet from anywhere. This is especially useful for dorm students who cannot physically come to Tehran just to use the university internet.
>
> All you need is an Iranian VPS (which is much more cost-effective than buying a VPN or even Pro internet packages in some cases). You can team up with friends to leave one computer running and pool multiple student accounts (including seniors or alumni) to split the costs. The built-in load balancer handles the rest!

---

## 📥 Download

Get the latest universal version for your system from the **[Releases Page](https://github.com/s-alireza/AUT-relay/releases/latest)**:

* 🚀 **[Download AUT Relay v2.0.0](https://github.com/s-alireza/AUT-relay/releases/latest)**

---

## 📋 Prerequisites

1. **An Iranian VPS**: You need a Linux VPS (Ubuntu/Debian) located in Iran (e.g., ArvanCloud, ParsPack).
2. **Python Installed**: You must have Python (3.4 or newer) installed on your Windows PC.
3. **AUT Network**: Your PC must be physically connected to the Amirkabir University network.

---

## 🚀 How to Setup

### Step 1: Initialize the Server

1. Extract the downloaded `AUT-Relay-Universal` folder.
2. Double-click **`Start_Server.bat`**.
3. The script will automatically open the **Initial Setup Wizard** in your browser at `http://127.0.0.1:3080`.
4. Follow the setup wizard to enter your initial AUT credentials, VPS IP, and create your Dashboard Admin account.

### Step 2: VPS Configuration (Manual Setup)

After completing the web wizard, you will receive a block of configuration for your VPS. SSH into your VPS and run the following commands to install and configure the FRP tunnel:

1. **Download and Extract FRP:**
   ```bash
   wget -O frp.tar.gz https://github.com/fatedier/frp/releases/download/v0.61.1/frp_0.61.1_linux_amd64.tar.gz
   tar -xzf frp.tar.gz && mv frp_0.61.1_linux_amd64 frp && chmod +x frp/frps
   ```
2. **Apply Configuration:**
   Run the following command, paste the generated configuration block from the web wizard, and press `Ctrl+D`:
   ```bash
   cat > frp/frps.toml
   ```
3. **Start the Service:**
   ```bash
   sudo setcap 'cap_net_bind_service=+ep' $(pwd)/frp/frps
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

### Step 3: Speed Optimization (BBR) [Recommended]

For maximum connection speed and lower latency, it is highly recommended to enable **BBR** (Bottleneck Bandwidth and Round-trip time) on your VPS. Run these commands on your VPS:

```bash
echo "net.core.default_qdisc=fq" | sudo tee -a /etc/sysctl.conf
echo "net.ipv4.tcp_congestion_control=bbr" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```
Verify it is active by running: `sysctl net.ipv4.tcp_congestion_control`. It should output `bbr`.

### Step 4: Connect & Manage

1. Install a **VLESS-compatible app** on your phone/device (e.g., **v2rayNG**, **v2rayN**, **Shadowrocket**, **Karing**).
2. In the Dashboard's **Users** tab, copy the subscription link or scan the QR code.
3. Connect and enjoy unrestricted access!

---

## 🌍 Bypassing IP Blocks (CDN Support)

If your VPS IP is blocked or restricted, you can bypass this by using a CDN (like ArvanCloud):
1. Buy a domain and register it in ArvanCloud.
2. Create two **A Records** pointing to your VPS IP:
   * **`@` (or a subdomain)** for the main tunnel (using port `8080`).
   * **`panel`** for the dashboard access (using port `8880`).
3. Enable the cloud/proxy icon ☁️ for these records to route your traffic through the CDN.

---

## 🤝 For Other Universities

The core proxy and load balancing mechanisms are highly adaptable. **Feel free to fork this project** and modify the `account_manager.py` login mechanisms to work with your own university's authentication portal!

---

## 🛠 Features

The system features a **Premium Management Console** accessible at `http://127.0.0.1:3080` (or remotely via `http://YOUR_VPS_IP:8880`).

### ⚡ High-Concurrency & Network Resilience
* **QUIC Transport (UDP):** The main tunnel operates over QUIC, completely bypassing TCP meltdowns on lossy networks and providing extreme resilience against ISP throttling.
* **Asynchronous DNS & Multi-Threading:** The bridge handles domain resolution asynchronously and features a fully multi-threaded API capable of scaling to 20+ concurrent users seamlessly.

### 📊 Overview & Telemetry

* **Live Topology:** Visualize your connection flow from the Campus node, through the Bridge, to the VPN Exit pool.
* **Host & Network Health:** Native, lightweight monitoring of the Host PC's CPU and RAM usage.
* **System Terminal:** Watch live, color-coded internal logs directly in your browser.

### ⚖️ Advanced Load Balancing & Account Rotation

The Bridge includes an intelligent `AccountRotator` that treats your AUT accounts like a resource pool:

* **Tiered Usage:** It burns through Daily limits first, then Weekly, Monthly, and finally Free tiers.
* **Session Limits:** You can configure a **Session Balance (GB)** (e.g., 1.0 GB). The bridge will rotate accounts every time a session hits this limit.
* **Dynamic Thresholds:** Customize the exact GB thresholds for all limits directly from the settings.

### 👥 Multi-User Management

Create individual users for your friends or roommates:

* Set exact Quota limits (e.g., 50 GB) and dynamic "Days Remaining" expiration dates.
* Enable or disable direct connections and CDN capabilities per user.
* Generate individual VLESS subscription links and QR codes.
* Accurate quota tracking synced directly from the backend to the dashboard.

### ⚙️ System Control Panel

* **Connectivity:** Manage your CDN/Dashboard Domain, VPS IP, and Port assignments.
* **Maintenance & Backup:** One-click **Export** and **Import** of your entire system configuration.
* **Danger Zone:** Wipe the entire system completely with one click.

---

## ❓ FAQ & Troubleshooting

**Q: The Dashboard is not loading. What should I do?**
A: Ensure Python is installed and added to your system PATH. Also, verify that `Start_Server.bat` is running without immediate crashes.

**Q: I see "VPS Tunnel Disconnected" in the dashboard. Why?**
A: Check that your VPS firewall allows inbound connections on ports **443** (FRP) and **8880** (Dashboard). Verify that your VPS is online.

**Q: I forgot my Dashboard Password. How can I reset it?**
A: Use the Danger Zone "System Wipe" if you can log in, or manually delete the `config` folder and run `Start_Server.bat` to recreate your setup from scratch.

---
*Created for unrestricted access. Future updates on the way!*
