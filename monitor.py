# monitor.py
"""
Optimized background network monitoring.

Features:
- Fast ping/jitter every interval
- Packet loss to Internet (8.8.8.8)
- Router (gateway) latency + loss
- Google Meet latency + loss
- ISP Hop 1 / Hop 2 latency + loss (from tracert -d -h 5 8.8.8.8)
- HTTP response latency (https://google.com)
- VPN detection + latency + loss
- SonicWall CPU (if SNMP is configured)
- Optional SonicWall extra SNMP metrics (sessions, WAN octets) – placeholders
- Local PC metrics: CPU %, Memory %, NIC throughput (up/down Mbps)
- Speedtest runs in separate thread (non-blocking, every N intervals)
- Stability score & Meeting quality score
- JST timestamps
- EXE-safe (subprocess windows hidden)
"""

import threading
import time
import csv
import os
import statistics
import subprocess
import platform
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Tuple, Optional, List

import psutil
from urllib.request import urlopen

from ping3 import ping
from speedtest import Speedtest
from PyQt6 import QtCore

from utils import (
    LOG_FILE_PATH,
    get_active_interface_info,
    get_public_ip,
    is_vpn_active,
    get_sonicwall_cpu_load,
    get_sonicwall_active_sessions,
    get_sonicwall_wan_octets,
)

import logging
logging.getLogger().disabled = True

# --- Hide Windows console windows for subprocess calls ---
HIDE_WINDOW = None
CREATE_NO_WINDOW = 0
if platform.system() == "Windows":
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
    HIDE_WINDOW = subprocess.STARTUPINFO()
    HIDE_WINDOW.dwFlags |= subprocess.STARTF_USESHOWWINDOW

# Hosts we test against
PUBLIC_PING_HOST = "8.8.8.8"          # Internet baseline
MEET_HOST = "meet.google.com"         # Main meeting platform

# If you know a specific VPN gateway IP, you could set it here.
VPN_TEST_HOST: Optional[str] = None


def get_http_latency(url: str = "https://google.com", timeout: float = 5.0) -> Optional[float]:
    """
    Simple HTTP latency check (ms). Returns None on error.
    """
    try:
        start = time.time()
        with urlopen(url, timeout=timeout) as resp:
            resp.read(100)  # small read to force real connection
        end = time.time()
        return round((end - start) * 1000.0, 2)
    except Exception:
        return None


class NetworkMonitor(QtCore.QObject):
    """
    Background network monitor.
    Emits data to the GUI using Qt signals.
    """

    data_collected = QtCore.pyqtSignal(dict)
    error_occurred = QtCore.pyqtSignal(str)

    def __init__(self, interval_seconds=60, parent=None):
        super().__init__(parent)
        self.interval_seconds = max(1, int(interval_seconds))

        self._stop_event = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

        # Speedtest management
        self._speedtest_running = False
        self._speedtest_result = None  # {"download": float, "upload": float}
        self._speedtest_counter = 0    # run every N intervals

        # ISP hop discovery (from tracert)
        self._isp_hops: List[str] = []
        self._detect_isp_hops()

        # NIC throughput tracking
        self._last_nic_counters = None
        self._last_nic_time: Optional[float] = None

        # SonicWall WAN octet tracking (optional)
        self._last_wan_octets = None
        self._last_wan_time: Optional[float] = None

    # ------------------------------------------------------------------ #
    # Start / Stop
    # ------------------------------------------------------------------ #
    def set_interval(self, seconds: int):
        with self._lock:
            self.interval_seconds = max(1, int(seconds))

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    # ------------------------------------------------------------------ #
    # Main Loop
    # ------------------------------------------------------------------ #
    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                sample = self._take_sample()
                self._append_to_log(sample)
                self.data_collected.emit(sample)
            except Exception as exc:
                self.error_occurred.emit(f"Monitoring error: {exc}")

            for _ in range(self.interval_seconds):
                if self._stop_event.is_set():
                    return
                time.sleep(1)

    # ------------------------------------------------------------------ #
    # Traceroute-based ISP hop discovery (one-time)
    # ------------------------------------------------------------------ #
    def _detect_isp_hops(self):
        """
        Runs 'tracert -d -h 5 8.8.8.8' once and stores the first 2 hop IPs.
        These are used as ISP Hop 1 / Hop 2 for latency & loss monitoring.
        """
        try:
            output = subprocess.check_output(
                ["tracert", "-d", "-h", "5", PUBLIC_PING_HOST],
                encoding="utf-8",
                errors="ignore",
                timeout=30,
                startupinfo=HIDE_WINDOW,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception:
            self._isp_hops = []
            return

        hops: List[str] = []
        for line in output.splitlines():
            line = line.strip()
            # Typical line:
            #  1     2 ms     2 ms     2 ms  221.253.69.145
            parts = line.split()
            if len(parts) >= 4 and parts[0].isdigit():
                ip = parts[-1]
                if ip.count(".") == 3:
                    hops.append(ip)

        self._isp_hops = hops[:2]

    # ------------------------------------------------------------------ #
    # Ping helper
    # ------------------------------------------------------------------ #
    def _ping_host(
        self,
        host: str,
        count: int = 5,
        timeout: float = 2.0,
    ) -> Tuple[Optional[float], Optional[float], float]:
        """
        Ping a host several times and return:
        (average_latency_ms, jitter_ms, packet_loss_pct)

        If all pings fail:
          - lat = None
          - jitter = None
          - loss = 100.0
        """
        latencies = []

        for _ in range(count):
            try:
                r = ping(host, unit="ms", timeout=timeout)
            except Exception:
                r = None

            if r is not None:
                latencies.append(r)
            time.sleep(0.1)

        success = len(latencies)
        loss_pct = round(100.0 * (count - success) / count, 1)

        if success == 0:
            return None, None, loss_pct

        avg = round(sum(latencies) / success, 2)
        jitter = round(statistics.pstdev(latencies), 2) if success > 1 else 0.0
        return avg, jitter, loss_pct

    # ------------------------------------------------------------------ #
    # Score helpers
    # ------------------------------------------------------------------ #
    def _compute_stability_score(
        self,
        internet_latency,
        internet_loss,
        router_loss,
        isp1_loss,
        isp2_loss,
        wifi_signal,
    ) -> int:
        """
        Compute a simple 0–100 "Network Stability Score".
        """
        score = 100

        # Internet loss
        if internet_loss is not None:
            if internet_loss >= 20:
                score -= 40
            elif internet_loss >= 5:
                score -= 20
            elif internet_loss >= 1:
                score -= 5

        # Internet latency
        if internet_latency is None:
            score -= 40
        else:
            if internet_latency > 300:
                score -= 30
            elif internet_latency > 150:
                score -= 15
            elif internet_latency > 80:
                score -= 5

        # Router and ISP losses
        for loss in (router_loss, isp1_loss, isp2_loss):
            if loss is not None:
                if loss >= 20:
                    score -= 15
                elif loss >= 5:
                    score -= 7

        # Wi-Fi signal
        if wifi_signal is not None:
            if wifi_signal < 30:
                score -= 20
            elif wifi_signal < 60:
                score -= 10

        return max(0, min(100, int(score)))

    def _compute_meeting_quality_score(
        self,
        meet_latency,
        meet_loss,
        jitter,
        internet_latency,
        internet_loss,
    ) -> int:
        """
        Compute a 0–100 "Meeting Quality Score" based mainly on Meet stats.
        """
        score = 100

        if meet_latency is None:
            score -= 30
        else:
            if meet_latency > 300:
                score -= 30
            elif meet_latency > 150:
                score -= 15
            elif meet_latency > 80:
                score -= 5

        if meet_loss is not None:
            if meet_loss >= 10:
                score -= 30
            elif meet_loss >= 3:
                score -= 15
            elif meet_loss >= 1:
                score -= 5

        if jitter is not None:
            if jitter > 50:
                score -= 20
            elif jitter > 20:
                score -= 10

        # Backup: also consider internet latency & loss
        if internet_latency is None or (internet_loss is not None and internet_loss >= 20):
            score -= 10

        return max(0, min(100, int(score)))

    # ------------------------------------------------------------------ #
    # Single measurement
    # ------------------------------------------------------------------ #
    def _take_sample(self) -> dict:
        timestamp = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M:%S")

        # 1. Internet baseline: public ping (8.8.8.8)
        latency_ms, jitter_ms, packet_loss_pct = self._ping_host(
            PUBLIC_PING_HOST,
            count=5,
            timeout=2.0,
        )

        # 2. Router / gateway ping (Wi-Fi / LAN health)
        iface_info = get_active_interface_info()
        gateway_ip = iface_info.get("gateway", "")
        router_latency_ms = None
        router_packet_loss_pct = None

        if gateway_ip:
            r_lat, r_jit, r_loss = self._ping_host(
                gateway_ip,
                count=5,
                timeout=1.5,
            )
            router_latency_ms = r_lat
            router_packet_loss_pct = r_loss

        # 3. ISP Hop 1 / Hop 2 (from tracert)
        isp_hop1_ip = self._isp_hops[0] if len(self._isp_hops) > 0 else ""
        isp_hop2_ip = self._isp_hops[1] if len(self._isp_hops) > 1 else ""

        isp_hop1_latency_ms = None
        isp_hop1_loss_pct = None
        isp_hop2_latency_ms = None
        isp_hop2_loss_pct = None

        if isp_hop1_ip:
            h1_lat, h1_jit, h1_loss = self._ping_host(
                isp_hop1_ip,
                count=3,
                timeout=1.5,
            )
            isp_hop1_latency_ms = h1_lat
            isp_hop1_loss_pct = h1_loss

        if isp_hop2_ip:
            h2_lat, h2_jit, h2_loss = self._ping_host(
                isp_hop2_ip,
                count=3,
                timeout=1.5,
            )
            isp_hop2_latency_ms = h2_lat
            isp_hop2_loss_pct = h2_loss

        # 4. Google Meet ping (platform edge / upstream ISP)
        meet_latency_ms = None
        meet_packet_loss_pct = None
        try:
            m_lat, m_jit, m_loss = self._ping_host(
                MEET_HOST,
                count=5,
                timeout=2.0,
            )
            meet_latency_ms = m_lat
            meet_packet_loss_pct = m_loss
        except Exception:
            meet_latency_ms = None
            meet_packet_loss_pct = None

        # 5. HTTP latency
        http_latency_ms = get_http_latency()

        # 6. VPN and SonicWall CPU (if applicable)
        vpn_active = is_vpn_active()
        vpn_latency_ms = None
        vpn_packet_loss_pct = None

        vpn_target = VPN_TEST_HOST or gateway_ip

        if vpn_active and vpn_target:
            v_lat, v_jit, v_loss = self._ping_host(
                vpn_target,
                count=5,
                timeout=1.5,
            )
            vpn_latency_ms = v_lat
            vpn_packet_loss_pct = v_loss

        sonicwall_cpu_pct = get_sonicwall_cpu_load()
        sonicwall_sessions = get_sonicwall_active_sessions()
        sonicwall_wan = get_sonicwall_wan_octets()

        # 7. Speedtest (background thread every 5 intervals)
        download_mbps = ""
        upload_mbps = ""

        if not hasattr(self, "_speedtest_counter"):
            self._speedtest_counter = 0
        self._speedtest_counter += 1

        if self._speedtest_counter >= 5:
            self._speedtest_counter = 0
            if not self._speedtest_running:
                threading.Thread(
                    target=self._run_speedtest_thread,
                    daemon=True,
                ).start()

        if isinstance(self._speedtest_result, dict):
            download_mbps = self._speedtest_result.get("download", "")
            upload_mbps = self._speedtest_result.get("upload", "")

        # 8. Interface info + Wi-Fi "signal"
        local_ip = iface_info.get("ip_address", "")
        dns = ";".join(iface_info.get("dns_servers", []))
        signal_strength = iface_info.get("signal_strength", "")
        iface_name = iface_info.get("name", "")

        # 9. Public IP
        public_ip = get_public_ip()

        # 10. Local PC metrics (CPU, Memory)
        cpu_pct = psutil.cpu_percent(interval=None)
        mem_pct = psutil.virtual_memory().percent

        # 11. Local NIC throughput (Mbps)
        nic_up_mbps = ""
        nic_down_mbps = ""
        if iface_name:
            counters = psutil.net_io_counters(pernic=True).get(iface_name)
            now_t = time.time()
            if counters and self._last_nic_counters and self._last_nic_time:
                dt = now_t - self._last_nic_time
                if dt > 0:
                    up_bps = (counters.bytes_sent - self._last_nic_counters.bytes_sent) / dt
                    down_bps = (counters.bytes_recv - self._last_nic_counters.bytes_recv) / dt
                    nic_up_mbps = round(up_bps / 1_000_000, 3)
                    nic_down_mbps = round(down_bps / 1_000_000, 3)
            self._last_nic_counters = counters
            self._last_nic_time = now_t

        # 12. SonicWall WAN throughput (if OIDs configured)
        sonicwall_wan_in_mbps = ""
        sonicwall_wan_out_mbps = ""
        if sonicwall_wan:
            now_wan = time.time()
            if self._last_wan_octets and self._last_wan_time:
                dtw = now_wan - self._last_wan_time
                if dtw > 0:
                    in_bps = (sonicwall_wan["in_octets"] - self._last_wan_octets["in_octets"]) * 8 / dtw
                    out_bps = (sonicwall_wan["out_octets"] - self._last_wan_octets["out_octets"]) * 8 / dtw
                    sonicwall_wan_in_mbps = round(in_bps / 1_000_000, 3)
                    sonicwall_wan_out_mbps = round(out_bps / 1_000_000, 3)
            self._last_wan_octets = sonicwall_wan
            self._last_wan_time = now_wan

        # 13. Status classification (uses latency + loss)
        if latency_ms is None or (packet_loss_pct is not None and packet_loss_pct >= 50.0):
            status = "DOWN"
        elif (latency_ms is not None and latency_ms > 200) or (
            packet_loss_pct is not None and packet_loss_pct >= 5.0
        ):
            status = "HIGH_LATENCY"
        else:
            status = "OK"

        # 14. Scores
        stability_score = self._compute_stability_score(
            internet_latency=latency_ms,
            internet_loss=packet_loss_pct,
            router_loss=router_packet_loss_pct,
            isp1_loss=isp_hop1_loss_pct,
            isp2_loss=isp_hop2_loss_pct,
            wifi_signal=signal_strength,
        )

        meeting_quality_score = self._compute_meeting_quality_score(
            meet_latency=meet_latency_ms,
            meet_loss=meet_packet_loss_pct,
            jitter=jitter_ms,
            internet_latency=latency_ms,
            internet_loss=packet_loss_pct,
        )

        sample = {
            "timestamp": timestamp,

            # Internet (8.8.8.8)
            "latency_ms": latency_ms if latency_ms is not None else "",
            "jitter_ms": jitter_ms if jitter_ms is not None else "",
            "packet_loss_pct": packet_loss_pct if packet_loss_pct is not None else "",
            "http_latency_ms": http_latency_ms if http_latency_ms is not None else "",

            # Router
            "router_latency_ms": router_latency_ms if router_latency_ms is not None else "",
            "router_packet_loss_pct": router_packet_loss_pct if router_packet_loss_pct is not None else "",

            # ISP Hops
            "isp_hop1_ip": isp_hop1_ip,
            "isp_hop1_latency_ms": isp_hop1_latency_ms if isp_hop1_latency_ms is not None else "",
            "isp_hop1_loss_pct": isp_hop1_loss_pct if isp_hop1_loss_pct is not None else "",
            "isp_hop2_ip": isp_hop2_ip,
            "isp_hop2_latency_ms": isp_hop2_latency_ms if isp_hop2_latency_ms is not None else "",
            "isp_hop2_loss_pct": isp_hop2_loss_pct if isp_hop2_loss_pct is not None else "",

            # Google Meet
            "meet_latency_ms": meet_latency_ms if meet_latency_ms is not None else "",
            "meet_packet_loss_pct": meet_packet_loss_pct if meet_packet_loss_pct is not None else "",

            # VPN
            "vpn_active": int(vpn_active),  # 1 or 0
            "vpn_latency_ms": vpn_latency_ms if vpn_latency_ms is not None else "",
            "vpn_packet_loss_pct": vpn_packet_loss_pct if vpn_packet_loss_pct is not None else "",

            # SonicWall
            "sonicwall_cpu_pct": sonicwall_cpu_pct if sonicwall_cpu_pct is not None else "",
            "sonicwall_sessions": sonicwall_sessions if sonicwall_sessions is not None else "",
            "sonicwall_wan_in_mbps": sonicwall_wan_in_mbps,
            "sonicwall_wan_out_mbps": sonicwall_wan_out_mbps,

            # Speedtest
            "download_mbps": download_mbps,
            "upload_mbps": upload_mbps,

            # Local machine
            "signal_strength": signal_strength,
            "local_ip": local_ip,
            "gateway": gateway_ip,
            "dns": dns,
            "public_ip": public_ip,
            "iface_name": iface_name,
            "cpu_pct": cpu_pct,
            "mem_pct": mem_pct,
            "nic_up_mbps": nic_up_mbps,
            "nic_down_mbps": nic_down_mbps,

            # Scores
            "stability_score": stability_score,
            "meeting_quality_score": meeting_quality_score,

            "status": status,
        }

        return sample

    # ------------------------------------------------------------------ #
    # Speedtest worker (separate thread)
    # ------------------------------------------------------------------ #
    def _run_speedtest_thread(self):
        self._speedtest_running = True
        try:
            try:
                st = Speedtest()
                st.get_best_server()
                download = st.download()
                upload = st.upload()
                self._speedtest_result = {
                    "download": round(download / 1_000_000, 2),
                    "upload": round(upload / 1_000_000, 2),
                }
            except Exception:
                # fully silent if speedtest fails
                self._speedtest_result = {"download": "", "upload": ""}
        finally:
            self._speedtest_running = False

    # ------------------------------------------------------------------ #
    # Logging
    # ------------------------------------------------------------------ #
    def _append_to_log(self, sample: dict):
        os.makedirs(os.path.dirname(LOG_FILE_PATH), exist_ok=True)
        file_exists = os.path.isfile(LOG_FILE_PATH)

        with open(LOG_FILE_PATH, mode="a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)

            if not file_exists:
                writer.writerow(
                    [
                        "timestamp",
                        "latency_ms",
                        "jitter_ms",
                        "packet_loss_pct",
                        "http_latency_ms",
                        "router_latency_ms",
                        "router_packet_loss_pct",
                        "isp_hop1_ip",
                        "isp_hop1_latency_ms",
                        "isp_hop1_loss_pct",
                        "isp_hop2_ip",
                        "isp_hop2_latency_ms",
                        "isp_hop2_loss_pct",
                        "meet_latency_ms",
                        "meet_packet_loss_pct",
                        "vpn_active",
                        "vpn_latency_ms",
                        "vpn_packet_loss_pct",
                        "sonicwall_cpu_pct",
                        "sonicwall_sessions",
                        "sonicwall_wan_in_mbps",
                        "sonicwall_wan_out_mbps",
                        "download_mbps",
                        "upload_mbps",
                        "signal_strength",
                        "local_ip",
                        "gateway",
                        "dns",
                        "public_ip",
                        "iface_name",
                        "cpu_pct",
                        "mem_pct",
                        "nic_up_mbps",
                        "nic_down_mbps",
                        "stability_score",
                        "meeting_quality_score",
                        "status",
                    ]
                )

            writer.writerow(
                [
                    sample["timestamp"],
                    sample["latency_ms"],
                    sample["jitter_ms"],
                    sample["packet_loss_pct"],
                    sample["http_latency_ms"],
                    sample["router_latency_ms"],
                    sample["router_packet_loss_pct"],
                    sample["isp_hop1_ip"],
                    sample["isp_hop1_latency_ms"],
                    sample["isp_hop1_loss_pct"],
                    sample["isp_hop2_ip"],
                    sample["isp_hop2_latency_ms"],
                    sample["isp_hop2_loss_pct"],
                    sample["meet_latency_ms"],
                    sample["meet_packet_loss_pct"],
                    sample["vpn_active"],
                    sample["vpn_latency_ms"],
                    sample["vpn_packet_loss_pct"],
                    sample["sonicwall_cpu_pct"],
                    sample["sonicwall_sessions"],
                    sample["sonicwall_wan_in_mbps"],
                    sample["sonicwall_wan_out_mbps"],
                    sample["download_mbps"],
                    sample["upload_mbps"],
                    sample["signal_strength"],
                    sample["local_ip"],
                    sample["gateway"],
                    sample["dns"],
                    sample["public_ip"],
                    sample["iface_name"],
                    sample["cpu_pct"],
                    sample["mem_pct"],
                    sample["nic_up_mbps"],
                    sample["nic_down_mbps"],
                    sample["stability_score"],
                    sample["meeting_quality_score"],
                    sample["status"],
                ]
            )
