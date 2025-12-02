# app.py
"""
Main GUI application for Internet Speed Monitoring.

Features:
- Dark GUI (style.qss)
- 4-section Current Status panel:
    1) Internet / ISP
    2) Router / SonicWall
    3) VPN
    4) Local PC
- Live graphs with PyQtGraph
- System tray icon (minimize to tray)
- Background monitoring with NetworkMonitor
- Graph generation from log.txt using matplotlib
- Notifications for high latency / Internet down
"""

import os
import sys
from typing import List

from PyQt6 import QtWidgets, QtGui, QtCore
import pyqtgraph as pg

from monitor import NetworkMonitor
from graph_generator import generate_graphs
from utils import BASE_DIR, LOG_FILE_PATH


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Internet Speed Monitor")
        self.resize(1400, 800)

        # Used for notifications throttling
        self._last_status = None

        # Central UI setup
        self._setup_ui()

        # System tray
        self._setup_tray()

        # Network monitor (interval from spinbox)
        self.monitor = NetworkMonitor(interval_seconds=self.interval_spin.value())
        self.monitor.data_collected.connect(self.on_data_collected)
        self.monitor.error_occurred.connect(self.on_monitor_error)
        self.monitor.start()

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #
    def _setup_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        main_layout = QtWidgets.QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # --- Current Status 4-section layout ---
        status_hbox = QtWidgets.QHBoxLayout()
        status_hbox.setSpacing(10)
        main_layout.addLayout(status_hbox)

        def add_row(form_layout: QtWidgets.QFormLayout, title: str) -> QtWidgets.QLabel:
            label_title = QtWidgets.QLabel(title)
            label_value = QtWidgets.QLabel("-")
            form_layout.addRow(label_title, label_value)
            return label_value

        # 1) Internet / ISP Group
        internet_group = QtWidgets.QGroupBox("Internet / ISP")
        internet_form = QtWidgets.QFormLayout()
        internet_group.setLayout(internet_form)

        self.lbl_latency = add_row(internet_form, "Latency (ms):")
        self.lbl_jitter = add_row(internet_form, "Jitter (ms):")
        self.lbl_http_latency = add_row(internet_form, "HTTP Latency (ms):")
        self.lbl_packet_loss = add_row(internet_form, "Internet Packet Loss (%):")
        self.lbl_download = add_row(internet_form, "Download (Mbps):")
        self.lbl_upload = add_row(internet_form, "Upload (Mbps):")
        self.lbl_public_ip = add_row(internet_form, "Public IP:")
        self.lbl_isp_hop1_ip = add_row(internet_form, "ISP Hop 1 IP:")
        self.lbl_isp_hop1_latency = add_row(internet_form, "ISP Hop 1 Latency (ms):")
        self.lbl_isp_hop1_loss = add_row(internet_form, "ISP Hop 1 Loss (%):")
        self.lbl_isp_hop2_ip = add_row(internet_form, "ISP Hop 2 IP:")
        self.lbl_isp_hop2_latency = add_row(internet_form, "ISP Hop 2 Latency (ms):")
        self.lbl_isp_hop2_loss = add_row(internet_form, "ISP Hop 2 Loss (%):")
        self.lbl_status = add_row(internet_form, "Status:")
        self.lbl_stability_score = add_row(internet_form, "Stability Score:")
        self.lbl_meeting_score = add_row(internet_form, "Meeting Quality:")

        status_hbox.addWidget(internet_group, stretch=1)

        # 2) Router / SonicWall Group
        router_group = QtWidgets.QGroupBox("Router / SonicWall")
        router_form = QtWidgets.QFormLayout()
        router_group.setLayout(router_form)

        self.lbl_gateway = add_row(router_form, "Gateway:")
        self.lbl_router_latency = add_row(router_form, "Router Latency (ms):")
        self.lbl_router_loss = add_row(router_form, "Router Packet Loss (%):")
        self.lbl_sonicwall_cpu = add_row(router_form, "SonicWall CPU (%):")
        self.lbl_sonicwall_sessions = add_row(router_form, "SonicWall Sessions:")
        self.lbl_sonicwall_wan_in = add_row(router_form, "SonicWall WAN In (Mbps):")
        self.lbl_sonicwall_wan_out = add_row(router_form, "SonicWall WAN Out (Mbps):")

        status_hbox.addWidget(router_group, stretch=1)

        # 3) VPN Group
        vpn_group = QtWidgets.QGroupBox("VPN")
        vpn_form = QtWidgets.QFormLayout()
        vpn_group.setLayout(vpn_form)

        self.lbl_vpn_active = add_row(vpn_form, "VPN Active:")
        self.lbl_vpn_latency = add_row(vpn_form, "VPN Latency (ms):")
        self.lbl_vpn_loss = add_row(vpn_form, "VPN Packet Loss (%):")

        status_hbox.addWidget(vpn_group, stretch=1)

        # 4) Local PC Group
        local_group = QtWidgets.QGroupBox("Local PC")
        local_form = QtWidgets.QFormLayout()
        local_group.setLayout(local_form)

        self.lbl_local_ip = add_row(local_form, "Local IP:")
        self.lbl_iface_name = add_row(local_form, "Interface Name:")
        self.lbl_dns = add_row(local_form, "DNS:")
        self.lbl_signal = add_row(local_form, "Wi-Fi Signal (%):")
        self.lbl_cpu_pct = add_row(local_form, "CPU Usage (%):")
        self.lbl_mem_pct = add_row(local_form, "Memory Usage (%):")
        self.lbl_nic_up = add_row(local_form, "NIC Upload (Mbps):")
        self.lbl_nic_down = add_row(local_form, "NIC Download (Mbps):")

        status_hbox.addWidget(local_group, stretch=1)

        # --- Middle: Live graphs ---
        self.graph_group = QtWidgets.QGroupBox("Live Graphs")
        self.graph_group.setObjectName("graph_group")
        graphs_layout = QtWidgets.QGridLayout()
        self.graph_group.setLayout(graphs_layout)

        # PyQtGraph dark theme
        pg.setConfigOption("background", (18, 18, 18))
        pg.setConfigOption("foreground", "w")

        self.latency_plot = pg.PlotWidget(title="Latency (ms)")
        self.download_plot = pg.PlotWidget(title="Download (Mbps)")
        self.upload_plot = pg.PlotWidget(title="Upload (Mbps)")
        self.signal_plot = pg.PlotWidget(title="Wi-Fi Signal (%)")

        graphs_layout.addWidget(self.latency_plot, 0, 0)
        graphs_layout.addWidget(self.download_plot, 0, 1)
        graphs_layout.addWidget(self.upload_plot, 1, 0)
        graphs_layout.addWidget(self.signal_plot, 1, 1)

        main_layout.addWidget(self.graph_group, stretch=1)

        # Data buffers
        self.max_points = 100
        self.latency_data: List[float] = []
        self.download_data: List[float] = []
        self.upload_data: List[float] = []
        self.signal_data: List[float] = []

        # Plot curves
        self.latency_curve = self.latency_plot.plot(pen="y")
        self.download_curve = self.download_plot.plot(pen="c")
        self.upload_curve = self.upload_plot.plot(pen="m")
        self.signal_curve = self.signal_plot.plot(pen="g")

        # --- Bottom: Controls ---
        control_group = QtWidgets.QGroupBox("Controls")
        control_layout = QtWidgets.QHBoxLayout()
        control_group.setLayout(control_layout)

        interval_label = QtWidgets.QLabel("Interval (seconds):")
        self.interval_spin = QtWidgets.QSpinBox()
        self.interval_spin.setRange(1, 3600)
        self.interval_spin.setValue(5)  # default 5 seconds
        self.interval_spin.valueChanged.connect(self.on_interval_changed)

        self.btn_generate_graphs = QtWidgets.QPushButton("Generate Graphs from log.txt")
        self.btn_generate_graphs.clicked.connect(self.on_generate_graphs)

        self.btn_show_log = QtWidgets.QPushButton("Open log.txt")
        self.btn_show_log.clicked.connect(self.on_open_log)

        self.btn_quit = QtWidgets.QPushButton("Quit")
        self.btn_quit.clicked.connect(self._quit_app)

        control_layout.addWidget(interval_label)
        control_layout.addWidget(self.interval_spin)
        control_layout.addStretch(1)
        control_layout.addWidget(self.btn_generate_graphs)
        control_layout.addWidget(self.btn_show_log)
        control_layout.addWidget(self.btn_quit)

        main_layout.addWidget(control_group)

        # Status bar
        self.statusBar().showMessage("Monitoring started…")

        # Right side of status bar
        creator_label = QtWidgets.QLabel("Created by Damith")
        creator_label.setStyleSheet("color: #aaaaaa; padding-right: 8px;")
        self.statusBar().addPermanentWidget(creator_label)

    # ------------------------------------------------------------------ #
    # Tray
    # ------------------------------------------------------------------ #
    def _setup_tray(self):
        self.tray = QtWidgets.QSystemTrayIcon(self)
        icon_path = os.path.join(BASE_DIR, "assets", "icon.png")
        if os.path.isfile(icon_path):
            self.tray.setIcon(QtGui.QIcon(icon_path))
        else:
            self.tray.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon))

        tray_menu = QtWidgets.QMenu()
        action_show = tray_menu.addAction("Show")
        action_quit = tray_menu.addAction("Quit")

        action_show.triggered.connect(self.show_main_window)
        action_quit.triggered.connect(self._quit_app)

        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason):
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
            self.show_main_window()

    def show_main_window(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    # ------------------------------------------------------------------ #
    # Events
    # ------------------------------------------------------------------ #
    def closeEvent(self, event: QtGui.QCloseEvent):
        """
        Override close: hide window and keep app running in tray.
        Use Quit button or tray menu → Quit to exit fully.
        """
        if self.tray.isVisible():
            self.hide()
            self.tray.showMessage(
                "Internet Speed Monitor",
                "Application is still running in the system tray.",
                QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                3000,
            )
            event.ignore()
        else:
            super().closeEvent(event)

    # ------------------------------------------------------------------ #
    # Monitor callbacks
    # ------------------------------------------------------------------ #
    @QtCore.pyqtSlot(dict)
    def on_data_collected(self, sample: dict):
        latency = sample["latency_ms"]
        jitter = sample["jitter_ms"]
        download = sample["download_mbps"]
        upload = sample["upload_mbps"]
        signal = sample["signal_strength"]

        # Internet / ISP
        self.lbl_latency.setText(str(latency) if latency != "" else "-")
        self.lbl_jitter.setText(str(jitter) if jitter != "" else "-")
        self.lbl_http_latency.setText(
            str(sample.get("http_latency_ms", "")) if sample.get("http_latency_ms", "") != "" else "-"
        )
        self.lbl_packet_loss.setText(
            str(sample.get("packet_loss_pct", "")) if sample.get("packet_loss_pct", "") != "" else "-"
        )
        self.lbl_download.setText(str(download) if download != "" else "-")
        self.lbl_upload.setText(str(upload) if upload != "" else "-")
        self.lbl_public_ip.setText(sample.get("public_ip", "") or "-")

        self.lbl_isp_hop1_ip.setText(sample.get("isp_hop1_ip", "") or "-")
        self.lbl_isp_hop1_latency.setText(
            str(sample.get("isp_hop1_latency_ms", "")) if sample.get("isp_hop1_latency_ms", "") != "" else "-"
        )
        self.lbl_isp_hop1_loss.setText(
            str(sample.get("isp_hop1_loss_pct", "")) if sample.get("isp_hop1_loss_pct", "") != "" else "-"
        )
        self.lbl_isp_hop2_ip.setText(sample.get("isp_hop2_ip", "") or "-")
        self.lbl_isp_hop2_latency.setText(
            str(sample.get("isp_hop2_latency_ms", "")) if sample.get("isp_hop2_latency_ms", "") != "" else "-"
        )
        self.lbl_isp_hop2_loss.setText(
            str(sample.get("isp_hop2_loss_pct", "")) if sample.get("isp_hop2_loss_pct", "") != "" else "-"
        )

        self.lbl_status.setText(sample.get("status", "") or "-")
        self.lbl_stability_score.setText(str(sample.get("stability_score", "-")))
        self.lbl_meeting_score.setText(str(sample.get("meeting_quality_score", "-")))

        # Router / SonicWall
        self.lbl_gateway.setText(sample.get("gateway", "") or "-")
        self.lbl_router_latency.setText(
            str(sample.get("router_latency_ms", "")) if sample.get("router_latency_ms", "") != "" else "-"
        )
        self.lbl_router_loss.setText(
            str(sample.get("router_packet_loss_pct", "")) if sample.get("router_packet_loss_pct", "") != "" else "-"
        )

        cpu = sample.get("sonicwall_cpu_pct", "")
        self.lbl_sonicwall_cpu.setText(str(cpu) if cpu != "" else "-")
        sess = sample.get("sonicwall_sessions", "")
        self.lbl_sonicwall_sessions.setText(str(sess) if sess != "" else "-")
        self.lbl_sonicwall_wan_in.setText(
            str(sample.get("sonicwall_wan_in_mbps", "")) if sample.get("sonicwall_wan_in_mbps", "") != "" else "-"
        )
        self.lbl_sonicwall_wan_out.setText(
            str(sample.get("sonicwall_wan_out_mbps", "")) if sample.get("sonicwall_wan_out_mbps", "") != "" else "-"
        )

        # VPN
        self.lbl_vpn_active.setText("Yes" if sample.get("vpn_active", 0) == 1 else "No")
        self.lbl_vpn_latency.setText(
            str(sample.get("vpn_latency_ms", "")) if sample.get("vpn_latency_ms", "") != "" else "-"
        )
        self.lbl_vpn_loss.setText(
            str(sample.get("vpn_packet_loss_pct", "")) if sample.get("vpn_packet_loss_pct", "") != "" else "-"
        )

        # Local PC
        self.lbl_local_ip.setText(sample.get("local_ip", "") or "-")
        self.lbl_iface_name.setText(sample.get("iface_name", "") or "-")
        self.lbl_dns.setText(sample.get("dns", "") or "-")
        self.lbl_signal.setText(str(signal) if signal not in ("", None) else "-")
        self.lbl_cpu_pct.setText(str(sample.get("cpu_pct", "")) if sample.get("cpu_pct", "") != "" else "-")
        self.lbl_mem_pct.setText(str(sample.get("mem_pct", "")) if sample.get("mem_pct", "") != "" else "-")
        self.lbl_nic_up.setText(
            str(sample.get("nic_up_mbps", "")) if sample.get("nic_up_mbps", "") != "" else "-"
        )
        self.lbl_nic_down.setText(
            str(sample.get("nic_down_mbps", "")) if sample.get("nic_down_mbps", "") != "" else "-"
        )

        # Graph update
        def push(buf: List[float], value):
            if value in ("", None):
                return
            try:
                buf.append(float(value))
            except (TypeError, ValueError):
                return
            if len(buf) > self.max_points:
                del buf[0]

        push(self.latency_data, latency)
        push(self.download_data, download)
        push(self.upload_data, upload)
        push(self.signal_data, signal)

        self.latency_curve.setData(list(range(len(self.latency_data))), self.latency_data)
        self.download_curve.setData(list(range(len(self.download_data))), self.download_data)
        self.upload_curve.setData(list(range(len(self.upload_data))), self.upload_data)
        self.signal_curve.setData(list(range(len(self.signal_data))), self.signal_data)

        # Notifications
        self._handle_notifications(sample.get("status", ""))

    @QtCore.pyqtSlot(str)
    def on_monitor_error(self, msg: str):
        self.statusBar().showMessage(msg)
        self.tray.showMessage(
            "Internet Speed Monitor - Error",
            msg,
            QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
            4000,
        )

    def _handle_notifications(self, status: str):
        """
        Show tray notifications when status changes.
        """
        if status == self._last_status:
            return

        if status == "HIGH_LATENCY":
            self.tray.showMessage(
                "Network warning",
                "Latency is very high (> 200 ms).",
                QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
                4000,
            )
        elif status == "DOWN":
            self.tray.showMessage(
                "Network error",
                "Internet connection appears to be down.",
                QtWidgets.QSystemTrayIcon.MessageIcon.Critical,
                4000,
            )

        self._last_status = status

    # ------------------------------------------------------------------ #
    # Control actions
    # ------------------------------------------------------------------ #
    def on_interval_changed(self, value: int):
        if hasattr(self, "monitor") and self.monitor:
            self.monitor.set_interval(value)
        self.statusBar().showMessage(f"Interval set to {value} seconds")

    def on_generate_graphs(self):
        paths = generate_graphs()
        if not paths:
            QtWidgets.QMessageBox.information(
                self,
                "Generate Graphs",
                "No log data found or log.txt is empty.",
            )
            return

        msg = "Generated graphs:\n" + "\n".join(paths)
        QtWidgets.QMessageBox.information(self, "Generate Graphs", msg)

    def on_open_log(self):
        if not os.path.isfile(LOG_FILE_PATH):
            with open(LOG_FILE_PATH, "w", encoding="utf-8") as f:
                f.write("")
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(LOG_FILE_PATH))

    def _quit_app(self):
        if hasattr(self, "monitor") and self.monitor:
            self.monitor.stop()
        self.tray.hide()
        QtWidgets.QApplication.instance().quit()


def load_stylesheet(app: QtWidgets.QApplication):
    style_path = os.path.join(BASE_DIR, "style.qss")
    if os.path.isfile(style_path):
        with open(style_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Internet Speed Monitor")
    load_stylesheet(app)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
