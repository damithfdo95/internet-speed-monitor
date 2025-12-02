# graph_generator.py
"""
Reads log.txt and generates PNG graphs using matplotlib.
"""

import csv
import os
from typing import List, Dict
from datetime import datetime

import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from utils import LOG_FILE_PATH, BASE_DIR


def read_log() -> List[Dict[str, str]]:
    if not os.path.isfile(LOG_FILE_PATH):
        return []

    rows: List[Dict[str, str]] = []
    with open(LOG_FILE_PATH, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def generate_graphs(output_dir: str | None = None) -> List[str]:
    rows = read_log()
    if not rows:
        return []

    if output_dir is None:
        output_dir = BASE_DIR

    timestamps = []
    latency = []
    jitter = []
    packet_loss = []
    router_latency = []
    router_loss = []
    meet_latency = []
    meet_loss = []
    download = []
    upload = []
    signal = []

    for row in rows:
        ts_str = row.get("timestamp", "")
        try:
            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        timestamps.append(ts)

        def parse_float(key: str):
            val = row.get(key, "")
            try:
                return float(val) if val != "" else None
            except ValueError:
                return None

        latency.append(parse_float("latency_ms"))
        jitter.append(parse_float("jitter_ms"))
        packet_loss.append(parse_float("packet_loss_pct"))
        router_latency.append(parse_float("router_latency_ms"))
        router_loss.append(parse_float("router_packet_loss_pct"))
        meet_latency.append(parse_float("meet_latency_ms"))
        meet_loss.append(parse_float("meet_packet_loss_pct"))
        download.append(parse_float("download_mbps"))
        upload.append(parse_float("upload_mbps"))
        signal.append(parse_float("signal_strength"))

    plt.style.use("dark_background")
    filepaths: List[str] = []

    def _time_plot(x, y, title, ylabel, filename):
        x_clean = []
        y_clean = []
        for xv, yv in zip(x, y):
            if yv is not None:
                x_clean.append(xv)
                y_clean.append(yv)
        if not x_clean:
            return None

        fig, ax = plt.subplots()
        ax.plot(x_clean, y_clean, marker="o", linestyle="-")
        ax.set_title(title)
        ax.set_xlabel("Time (JST)")
        ax.set_ylabel(ylabel)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M\n%Y-%m-%d"))
        fig.autofmt_xdate()

        path = os.path.join(output_dir, filename)
        fig.savefig(path, dpi=120, bbox_inches="tight")
        plt.close(fig)
        return path

    mappings = [
        (latency, "Internet Latency (ms)", "Latency (ms)", "latency.png"),
        (jitter, "Internet Jitter (ms)", "Jitter (ms)", "jitter.png"),
        (packet_loss, "Internet Packet Loss (%)", "Loss (%)", "packet_loss.png"),
        (router_latency, "Router Latency (ms)", "Latency (ms)", "router_latency.png"),
        (router_loss, "Router Packet Loss (%)", "Loss (%)", "router_loss.png"),
        (meet_latency, "Google Meet Latency (ms)", "Latency (ms)", "meet_latency.png"),
        (meet_loss, "Google Meet Packet Loss (%)", "Loss (%)", "meet_loss.png"),
        (download, "Download (Mbps)", "Download (Mbps)", "download.png"),
        (upload, "Upload (Mbps)", "Upload (Mbps)", "upload.png"),
        (signal, "Wi-Fi Signal (%)", "Signal (%)", "signal.png"),
    ]

    for series, title, ylabel, fname in mappings:
        path = _time_plot(timestamps, series, title, ylabel, fname)
        if path:
            filepaths.append(path)

    return filepaths
