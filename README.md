# Internet Speed Monitor

A professional Windows desktop application for real-time network performance monitoring.

Built with **Python 3.13 · PyQt6 · PyInstaller**, it provides detailed visibility into:

---

## 🚀 Features

### 🌐 Internet Monitoring
- Internet latency (8.8.8.8)
- HTTP latency
- ISP hop latency (traceroute)
- Packet loss (%)
- Google Meet edge latency

### 📡 Router / Firewall
- Router (gateway) latency
- SonicWall metrics via SNMP:
  - CPU load (%)
  - Active sessions
  - WAN in/out traffic (Mbps)

### 🔐 VPN Monitoring
- VPN active detection
- VPN latency and packet loss

### 💻 Local PC Metrics
- CPU usage (%)
- RAM usage (%)
- NIC upload/download (Mbps)
- Wi-Fi signal strength (%)

### 📈 Additional
- Live PyQtGraph charts
- Dark mode GUI (style.qss)
- System tray support
- Auto-logging to `log.txt`
- Speedtest (background thread)
- Graph generation (matplotlib)

---

## 📦 Installation

```bash
git clone https://github.com/damithfdo95/internet-speed-monitor.git
cd internet-speed-monitor
pip install -r requirements.txt
```

---

## ▶️ Run the Application

```bash
python app.py
```

---

## 🛠 Build EXE (Windows)

```bash
pyinstaller app.spec --clean --noconfirm
```
Output executable:
dist/Internet Speed Monitor.exe

---

## 📊 Generate Graphs
Output files saved inside:
```bash
graphs/
```

---

## 📁 Project Structure

```pgsql
app.py               → Main PyQt UI
monitor.py           → Background monitoring engine
utils.py             → Network, SNMP, system helpers
graph_generator.py   → Matplotlib graph creation
style.qss            → Dark theme stylesheet
app.spec             → PyInstaller build script
assets/              → Icons & resources
log.txt              → Auto-generated network logs

```

---

## 📜 License

MIT License — free for personal and commercial use.

---

## 👤 Author

Created by Damith Fdo

---