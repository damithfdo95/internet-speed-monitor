# utils.py
"""
Helper utilities for network info, paths, IP detection, and SonicWall SNMP.

NOTE:
- All subprocess calls are hidden (no console popup) when running as EXE.
- SonicWall extra SNMP metrics (sessions, WAN throughput) are OPTIONAL.
  Fill in the correct OIDs from your SonicWall MIB if you want to use them.
"""

import os
import sys
import platform
import subprocess
import re
import socket
from typing import Dict, List, Optional
from urllib.request import urlopen
from urllib.error import URLError

import psutil

# --- Hide Windows console windows for subprocess calls ---
HIDE_WINDOW = None
CREATE_NO_WINDOW = 0
if platform.system() == "Windows":
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
    HIDE_WINDOW = subprocess.STARTUPINFO()
    HIDE_WINDOW.dwFlags |= subprocess.STARTF_USESHOWWINDOW

if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS   # PyInstaller temp folder
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    
LOG_FILE_PATH = os.path.join(BASE_DIR, "log.txt")

# --- SonicWall SNMP settings (edit for your environment) ---
# This should be the LAN IP of your SonicWall (very likely your gateway)
SONICWALL_SNMP_IP = "192.168.227.254"  # change if different
SONICWALL_SNMP_PORT = 161
SONICWALL_SNMP_COMMUNITY = "public"    # change to your SNMP community string

# Main known OID we already used:
SONICWALL_OID_CPU = "1.3.6.1.4.1.8741.1.3.1.3.0"

# Optional extra OIDs (FILL THESE FROM YOUR DEVICE / MIB IF YOU WANT TO USE THEM)
# leaving them empty means the functions will just return None.
SONICWALL_OID_ACTIVE_SESSIONS = ""   # e.g. total concurrent sessions
SONICWALL_OID_WAN_IN_OCTETS = ""     # e.g. ifInOctets index for WAN
SONICWALL_OID_WAN_OUT_OCTETS = ""    # e.g. ifOutOctets index for WAN

try:
    from pysnmp.hlapi import (
        SnmpEngine,
        CommunityData,
        UdpTransportTarget,
        ContextData,
        ObjectType,
        ObjectIdentity,
        getCmd,
    )
    _HAS_PYSNMP = True
except ImportError:
    _HAS_PYSNMP = False


# --------------------------------------------------------------------------- #
# SNMP helpers
# --------------------------------------------------------------------------- #
def _snmp_get_numeric(oid: str, timeout: int = 3) -> Optional[float]:
    """
    Simple SNMP GET returning numeric value, or None on any error.
    """
    if not _HAS_PYSNMP:
        return None
    if not SONICWALL_SNMP_IP or not SONICWALL_SNMP_COMMUNITY:
        return None
    if not oid:
        return None

    try:
        iterator = getCmd(
            SnmpEngine(),
            CommunityData(SONICWALL_SNMP_COMMUNITY, mpModel=1),  # v2c
            UdpTransportTarget((SONICWALL_SNMP_IP, SONICWALL_SNMP_PORT), timeout=timeout, retries=1),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
        )

        errorIndication, errorStatus, errorIndex, varBinds = next(iterator)

        if errorIndication or errorStatus:
            return None

        for _, val in varBinds:
            try:
                return float(val)
            except (ValueError, TypeError):
                return None
    except Exception:
        return None

    return None


def get_sonicwall_cpu_load(timeout: int = 3) -> Optional[float]:
    """
    Query SonicWall firewall via SNMP and return CPU usage in percent.

    Returns:
      float 0-100, or None if SNMP not available / error.
    """
    return _snmp_get_numeric(SONICWALL_OID_CPU, timeout=timeout)


def get_sonicwall_active_sessions(timeout: int = 3) -> Optional[float]:
    """
    Optional: total active sessions on SonicWall.
    Requires SONICWALL_OID_ACTIVE_SESSIONS to be set correctly.
    """
    return _snmp_get_numeric(SONICWALL_OID_ACTIVE_SESSIONS, timeout=timeout)


def get_sonicwall_wan_octets(timeout: int = 3) -> Optional[Dict[str, float]]:
    """
    Optional: WAN traffic counters (octets).
    Requires SONICWALL_OID_WAN_IN_OCTETS / SONICWALL_OID_WAN_OUT_OCTETS.

    Returns:
      {"in_octets": float, "out_octets": float} or None.
    """
    in_val = _snmp_get_numeric(SONICWALL_OID_WAN_IN_OCTETS, timeout=timeout)
    out_val = _snmp_get_numeric(SONICWALL_OID_WAN_OUT_OCTETS, timeout=timeout)
    if in_val is None or out_val is None:
        return None
    return {"in_octets": in_val, "out_octets": out_val}


# --------------------------------------------------------------------------- #
# VPN detection
# --------------------------------------------------------------------------- #
def is_vpn_active() -> bool:
    """
    Heuristic VPN detection.
    Looks for any UP interface whose name suggests VPN / SonicWall / tunnel.
    This works well for SonicWall Mobile Connect / NetExtender and many others.
    """
    stats = psutil.net_if_stats()
    for ifname, s in stats.items():
        if not s.isup:
            continue
        lname = ifname.lower()
        if any(keyword in lname for keyword in [
            "sonicwall",
            "vpn",
            "wan miniport",
            "ppp",
        ]):
            return True
    return False


# --------------------------------------------------------------------------- #
# Active interface + ipconfig parsing (Japanese Windows aware)
# --------------------------------------------------------------------------- #
def get_active_interface_info() -> Dict[str, object]:
    """
    Best possible interface selector for Japanese Windows.
    - Correctly ignores VirtualBox, Hyper-V, WSL, Wi-Fi Direct, etc.
    - Correctly detects Japanese labels like:
        デフォルト ゲートウェイ
        DNS サーバー
    - Selects only REAL uplink interface that has:
        • IPv4
        • Gateway present
    """

    info = {
        "name": None,
        "ip_address": None,
        "is_wifi": False,
        "signal_strength": None,
        "gateway": None,
        "dns_servers": [],
    }

    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()

    # ------------ 1. Virtual adapter filter ------------
    def is_virtual(ifname: str) -> bool:
        lname = ifname.lower()
        return any(key in lname for key in [
            "virtual",
            "veth",
            "vethernet",
            "hyper-v",
            "wsl",
            "loopback",
            "vmware",
            "virtualbox",
            "host-only",        # VirtualBox host-only
            "wi-fi direct",     # MS Wi-Fi Direct
            "bluetooth",
        ])

    # ------------ 2. Find real adapters with IPv4 ------------
    ipv4_candidates = []

    for ifname, iface_addrs in addrs.items():
        if is_virtual(ifname):
            continue

        st = stats.get(ifname)
        if not st or not st.isup:
            continue

        ipv4 = None
        for addr in iface_addrs:
            if addr.family == socket.AF_INET and addr.address != "127.0.0.1":
                ipv4 = addr.address
                break

        if ipv4:
            ipv4_candidates.append((ifname, ipv4))

    if not ipv4_candidates:
        return info

    # ------------ 3. Parse ipconfig /all (JP and EN compatible) ------------
    try:
        raw = subprocess.check_output(
            ["ipconfig", "/all"],
            encoding="utf-8",
            errors="ignore",
            startupinfo=HIDE_WINDOW,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        return info

    blocks = re.split(r"\r?\n\r?\n", raw)

    def extract_gateway_dns(block: str):
        """Extract gateway + dns for this block."""
        gateway = None
        dns_list: List[str] = []

        # JP format:
        # デフォルト ゲートウェイ . . . . . . .: 192.168.227.254
        gw_jp = re.findall(r"デフォルト\s*ゲートウェイ.*?(\d+\.\d+\.\d+\.\d+)", block)

        # EN format:
        gw_en = re.findall(r"Default Gateway.*?(\d+\.\d+\.\d+\.\d+)", block)

        gw_all = gw_jp + gw_en

        if gw_all:
            gateway = gw_all[0]

        # DNS JP:
        # DNS サーバー. . . . . . . . .: 61.122.116.132
        #                             61.122.116.165
        dns_jp = re.findall(r"DNS\s*サーバー.*?(\d+\.\d+\.\d+\.\d+)", block)
        dns_jp2 = re.findall(r"^\s*(\d+\.\d+\.\d+\.\d+)$", block, re.MULTILINE)

        # DNS EN:
        dns_en = re.findall(r"DNS Servers.*?(\d+\.\d+\.\d+\.\d+)", block)

        dns_list = dns_jp + dns_jp2 + dns_en
        dns_list = list(dict.fromkeys(dns_list))

        return gateway, dns_list

    # ------------ 4. Match candidates to ipconfig blocks ------------
    chosen = None

    for ifname, ip in ipv4_candidates:
        for block in blocks:
            if ifname in block or ip in block:
                gateway, dns = extract_gateway_dns(block)
                if gateway:
                    chosen = (ifname, ip, gateway, dns)
                    break
        if chosen:
            break

    # If nothing has a gateway (rare), fallback to last real adapter
    if not chosen:
        ifname, ip = ipv4_candidates[-1]
        info["name"] = ifname
        info["ip_address"] = ip
        return info

    ifname, ipaddr, gateway, dns_list = chosen

    # ------------ 5. Build return info ------------
    info["name"] = ifname
    info["ip_address"] = ipaddr
    info["gateway"] = gateway
    info["dns_servers"] = dns_list
    info["is_wifi"] = ("wi-fi" in ifname.lower() or "wlan" in ifname.lower())

    st = stats.get(ifname)
    if st and st.speed:
        # simple heuristic
        max_speed = 300.0
        info["signal_strength"] = int(min(100, max(0, (st.speed / max_speed) * 100)))

    return info


def _parse_ipconfig(ip_address: Optional[str]) -> (Optional[str], List[str]):
    """
    Old helper (kept for compatibility).
    Robust parser for `ipconfig` that handles:
    - multi-line gateway values
    - IPv6 before IPv4
    - localized formats (JP/EN)
    - multiple DNS entries
    """

    try:
        output = subprocess.check_output(
            ["ipconfig"],
            encoding="utf-8",
            errors="ignore",
            startupinfo=HIDE_WINDOW,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        return None, []

    # Normalize everything
    lines = [line.strip() for line in output.splitlines() if line.strip()]

    gateway = None
    dns_servers: List[str] = []
    in_correct_block = False
    dns_section = False

    for line in lines:
        # Check if we entered correct adapter block
        if ip_address and ip_address in line:
            in_correct_block = True
        elif "adapter" in line.lower():
            # new adapter block
            in_correct_block = False
            dns_section = False

        if not in_correct_block:
            continue

        # --- Default Gateway ---
        if line.lower().startswith("default gateway"):
            parts = re.findall(r"(\d+\.\d+\.\d+\.\d+)", line)
            if parts:
                gateway = parts[0]
            else:
                continue

        if gateway is None:
            ipv4 = re.search(r"(\d+\.\d+\.\d+\.\d+)", line)
            if ipv4:
                gateway = ipv4.group(1)

        # --- DNS Servers ---
        if "dns servers" in line.lower():
            dns_section = True
            ipv4 = re.findall(r"(\d+\.\d+\.\d+\.\d+)", line)
            dns_servers.extend(ipv4)
            continue

        if dns_section:
            ipv4 = re.findall(r"(\d+\.\d+\.\d+\.\d+)", line)
            if ipv4:
                dns_servers.extend(ipv4)
            else:
                dns_section = False

    dns_servers = list(dict.fromkeys(dns_servers))

    return gateway, dns_servers


# --------------------------------------------------------------------------- #
# Public IP
# --------------------------------------------------------------------------- #
def get_public_ip(timeout: int = 5) -> Optional[str]:
    """
    Get public IP from ipify. Returns None if unreachable.
    """
    try:
        with urlopen("https://api.ipify.org", timeout=timeout) as resp:
            return resp.read().decode("utf-8").strip()
    except URLError:
        return None
    except Exception:
        return None
