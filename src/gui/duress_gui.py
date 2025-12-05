#!/usr/bin/env python3
import sys
import os
import json
import uuid
import socket
import subprocess  # for auto-detect in DeviceDialog and global

from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QSystemTrayIcon,
    QMenu,
    QAction,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QDialog,
    QFormLayout,
    QLineEdit,
    QComboBox,
    QCheckBox,
    QMessageBox,
    QTextEdit,
)
from PyQt5.QtGui import (
    QIcon,
    QPainter,
    QPixmap,
    QColor,
)
from PyQt5.QtCore import Qt, QTimer

SOCKET_CMD = "/tmp/luks-duress.sock"
SOCKET_GUI = "/tmp/luks-duress_gui"
SOCKET_LOG_GUI = "/tmp/luks-duress_log_gui"  # daemon sends logs to this path

# Circular buffer limit for daemon logs
LOG_BUFFER_LIMIT = 5000


def send_command(cmd: str):
    """Send a simple text command to the daemon via Unix datagram socket."""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.sendto(cmd.encode(), SOCKET_CMD)
        sock.close()
        print(f"[GUI] Sent command: {cmd}")
    except OSError as e:
        print(f"[GUI] Error sending command '{cmd}': {e}")


def make_circle_icon(color: QColor, size: int = 16) -> QIcon:
    """Create a simple colored circle icon (for tray)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(color)
    painter.setPen(Qt.NoPen)
    radius = size // 2 - 1
    painter.drawEllipse(1, 1, radius * 2, radius * 2)
    painter.end()

    return QIcon(pixmap)


class DeviceDialog(QDialog):
    MODES = ["insert", "remove", "any"]
    ACTIONS = ["wipe", "lock", "shutdown", "command"]

    def __init__(self, device: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Device" if device.get("id") else "Register Device")
        self.device = device.copy()

        layout = QFormLayout()

        self.name_edit = QLineEdit(self.device.get("name", ""))
        self.vid_edit = QLineEdit(self.device.get("vid", ""))
        self.pid_edit = QLineEdit(self.device.get("pid", ""))
        self.serial_edit = QLineEdit(self.device.get("serial", ""))

        # wipe target path field (per-device)
        self.wipe_target_edit = QLineEdit(self.device.get("wipe_target", ""))

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(self.MODES)
        val = self.device.get("mode", "insert")
        if val in self.MODES:
            self.mode_combo.setCurrentText(val)

        self.action_combo = QComboBox()
        self.action_combo.addItems(self.ACTIONS)
        aval = self.device.get("action", "wipe")
        if aval in self.ACTIONS:
            self.action_combo.setCurrentText(aval)

        self.custom_cmd_edit = QLineEdit(self.device.get("custom_cmd", ""))

        self.test_check = QCheckBox("Test mode (simulate only)")
        self.test_check.setChecked(bool(self.device.get("test_mode", True)))

        self.active_check = QCheckBox("Active")
        self.active_check.setChecked(bool(self.device.get("active", True)))

        layout.addRow("Name:", self.name_edit)
        layout.addRow("VID (hex):", self.vid_edit)
        layout.addRow("PID (hex):", self.pid_edit)
        layout.addRow("Serial:", self.serial_edit)
        layout.addRow("Wipe Target Device:", self.wipe_target_edit)

        # Optional convenience: auto-detect button
        auto_btn = QPushButton("Auto-detect System LUKS Device")
        auto_btn.clicked.connect(self.auto_detect_wipe_target)
        layout.addRow(auto_btn)

        layout.addRow("Mode:", self.mode_combo)
        layout.addRow("Action:", self.action_combo)
        layout.addRow("Custom command:", self.custom_cmd_edit)
        layout.addRow(self.test_check)
        layout.addRow(self.active_check)

        btn_box = QHBoxLayout()
        save_btn = QPushButton("Save")
        cancel_btn = QPushButton("Cancel")
        save_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        btn_box.addWidget(save_btn)
        btn_box.addWidget(cancel_btn)
        layout.addRow(btn_box)

        self.setLayout(layout)

    def auto_detect_wipe_target(self):
        """
        Try to auto-detect the physical device backing the root filesystem.
        Fills wipe_target_edit with something like /dev/nvme0n1 or /dev/sda.
        """
        try:
            src = subprocess.check_output(
                ["findmnt", "-no", "SOURCE", "/"],
                text=True
            ).strip()

            base = subprocess.check_output(
                ["lsblk", "-no", "PKNAME", src],
                text=True
            ).strip()

            if base:
                dev = f"/dev/{base}"
            else:
                dev = src

            self.wipe_target_edit.setText(dev)
        except Exception as e:
            QMessageBox.warning(self, "Auto-detect failed", str(e))

    def get_device(self):
        d = self.device.copy()
        if not d.get("id"):
            d["id"] = str(uuid.uuid4())

        d["name"] = self.name_edit.text().strip()
        d["vid"] = self.vid_edit.text().strip()
        d["pid"] = self.pid_edit.text().strip()
        d["serial"] = self.serial_edit.text().strip()
        d["wipe_target"] = self.wipe_target_edit.text().strip()
        d["mode"] = self.mode_combo.currentText()
        d["action"] = self.action_combo.currentText()
        d["custom_cmd"] = self.custom_cmd_edit.text().strip()
        d["test_mode"] = self.test_check.isChecked()
        d["active"] = self.active_check.isChecked()
        return d


class DevLogWindow(QWidget):
    def __init__(self):
        # Make this a true top-level window (parent=None)
        super().__init__(None)
        self.setWindowTitle("Daemon Log Output")
        # start a bit larger to avoid crowding the main UI
        self.resize(900, 600)

        layout = QVBoxLayout()
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setStyleSheet("""
            background-color: #111;
            color: #0f0;
            font-family: monospace;
            font-size: 11pt;
        """)

        # Make it behave like a terminal: no wrap + scrollbars as needed
        # QTextEdit uses setLineWrapMode; use NoWrap to prevent clipping
        try:
            self.text.setLineWrapMode(QTextEdit.NoWrap)
        except Exception:
            # keep best-effort compatibility
            pass
        self.text.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        layout.addWidget(self.text)
        self.setLayout(layout)

    def append_line(self, line: str):
        # append keeps existing behavior and scrolls to bottom
        self.text.append(line)
        self.text.moveCursor(self.text.textCursor().End)


class DuressMainWindow(QMainWindow):
    GLOBAL_MODES = ["off", "insert", "remove", "any"]
    GLOBAL_ACTIONS = ["wipe", "lock", "shutdown", "command"]

    def __init__(self):
        super().__init__()

        self.setWindowTitle("LUKS Duress Control")
        self.resize(750, 550)

        # Runtime state
        self.armed = False
        self.devices = []
        self.global_rules = {
            "active": False,
            "mode": "any",
            "action": "lock",
            "custom_cmd": "",
            "test_mode": True,
            "wipe_target": "",
        }

        self.last_event = None
        self.request_mode = None   # None / "identify" / "register"

        # Daemon log buffer + dev window
        self.daemon_log_buffer = []   # lines from daemon (circular)
        self.dev_window = None

        # --- Top-level layout ---
        central = QWidget()
        main_layout = QVBoxLayout()

        # ========== STATUS + ARM ==========
        status_layout = QVBoxLayout()
        self.status_label = QLabel("Status: DISARMED")
        self.status_label.setAlignment(Qt.AlignCenter)

        self.toggle_button = QPushButton("ARM")
        self.toggle_button.clicked.connect(self.toggle_arm_state)
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.toggle_button)

        # Dev button (open daemon log window)
        btn_dev = QPushButton("Dev")
        btn_dev.clicked.connect(self.open_dev_window)
        status_layout.addWidget(btn_dev)

        # ========== GLOBAL RULES SECTION ==========
        global_box = QVBoxLayout()
        lbl = QLabel("Global USB Trigger")
        lbl.setStyleSheet("font-weight: bold; font-size: 12pt;")
        global_box.addWidget(lbl)

        row1 = QHBoxLayout()
        self.global_mode_combo = QComboBox()
        self.global_mode_combo.addItems(self.GLOBAL_MODES)
        self.global_mode_combo.currentTextChanged.connect(self.on_global_changed)

        self.global_action_combo = QComboBox()
        self.global_action_combo.addItems(self.GLOBAL_ACTIONS)
        self.global_action_combo.currentTextChanged.connect(self.on_global_changed)

        self.global_test_check = QCheckBox("Test mode")
        self.global_test_check.setChecked(True)
        self.global_test_check.stateChanged.connect(self.on_global_changed)

        row1.addWidget(QLabel("Mode:"))
        row1.addWidget(self.global_mode_combo)
        row1.addWidget(QLabel("Action:"))
        row1.addWidget(self.global_action_combo)
        row1.addWidget(self.global_test_check)

        # Row 2: global wipe target and auto-detect
        row2 = QHBoxLayout()
        self.global_wipe_target_edit = QLineEdit(self.global_rules.get("wipe_target", ""))
        self.global_wipe_target_edit.editingFinished.connect(self.on_global_changed)

        btn_global_auto = QPushButton("Auto-detect System LUKS Device")
        btn_global_auto.clicked.connect(self.auto_detect_global_wipe_target)

        row2.addWidget(QLabel("Wipe target:"))
        row2.addWidget(self.global_wipe_target_edit)
        row2.addWidget(btn_global_auto)

        # Row 3: global custom command (used when action = command)
        row3 = QHBoxLayout()
        self.global_custom_cmd_edit = QLineEdit(self.global_rules.get("custom_cmd", ""))
        self.global_custom_cmd_edit.editingFinished.connect(self.on_global_changed)

        row3.addWidget(QLabel("Custom command:"))
        row3.addWidget(self.global_custom_cmd_edit)

        global_box.addLayout(row1)
        global_box.addLayout(row2)
        global_box.addLayout(row3)

        # Visual separator
        sep = QWidget()
        sep.setFixedHeight(2)
        sep.setStyleSheet("background: #444444;")
        global_box.addWidget(sep)

        # ========== LAST USB EVENT ==========
        usb_layout = QHBoxLayout()
        self.last_usb_label = QLabel("Last USB event: (none)")
        self.last_usb_label.setStyleSheet("color: gray;")
        btn_ident = QPushButton("Identify Last USB")
        btn_reg = QPushButton("Register Last USB")
        btn_ident.clicked.connect(self.on_identify_usb)
        btn_reg.clicked.connect(self.on_register_usb)
        usb_layout.addWidget(self.last_usb_label)
        usb_layout.addWidget(btn_ident)
        usb_layout.addWidget(btn_reg)

        # ========== DEVICE MANAGER ==========
        dm_layout = QVBoxLayout()
        dm_label = QLabel("Per-device Rules")
        dm_label.setStyleSheet("font-weight: bold; font-size: 12pt;")

        self.device_table = QTableWidget(0, 5)
        self.device_table.setHorizontalHeaderLabels(
            ["Active", "Name", "Mode", "Action", "Test"]
        )
        header = self.device_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.device_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.device_table.itemChanged.connect(self.on_device_item_changed)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        edit_btn = QPushButton("Edit Selected")
        delete_btn = QPushButton("Delete Selected")
        refresh_btn.clicked.connect(self.request_devices)
        edit_btn.clicked.connect(self.on_edit_device)
        delete_btn.clicked.connect(self.on_delete_device)
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(edit_btn)
        btn_row.addWidget(delete_btn)

        dm_layout.addWidget(dm_label)
        dm_layout.addWidget(self.device_table)
        dm_layout.addLayout(btn_row)

        # ========== Assemble all ==========
        main_layout.addLayout(status_layout)
        main_layout.addSpacing(8)
        main_layout.addLayout(global_box)
        main_layout.addSpacing(8)
        main_layout.addLayout(usb_layout)
        main_layout.addSpacing(8)
        main_layout.addLayout(dm_layout)

        central.setLayout(main_layout)
        self.setCentralWidget(central)

        # Keep UI comfortably compact
        self.setMinimumWidth(450)
        self.setMaximumWidth(650)
        self.resize(550, 600)

        # ========== Tray Icon ==========
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(make_circle_icon(QColor(60, 179, 113)))
        tray_menu = QMenu()
        act_arm = QAction("Arm", self)
        act_disarm = QAction("Disarm", self)
        act_show = QAction("Show/Hide Window", self)
        act_quit = QAction("Quit", self)
        act_arm.triggered.connect(self.arm)
        act_disarm.triggered.connect(self.disarm)
        act_show.triggered.connect(self.toggle_window_visibility)
        act_quit.triggered.connect(self.quit_app)
        tray_menu.addAction(act_arm)
        tray_menu.addAction(act_disarm)
        tray_menu.addSeparator()
        tray_menu.addAction(act_show)
        tray_menu.addSeparator()
        tray_menu.addAction(act_quit)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

        # Click tray icon to toggle window
        self.tray_icon.activated.connect(self.on_tray_activated)

        # Response socket
        self.recv_socket = None
        self.poll_timer = None
        self.setup_response_socket()

        # Log socket (daemon -> GUI)
        self.log_socket = None
        self.log_timer = None
        self.setup_log_socket()

        # Load initial settings
        self.update_ui()
        self.request_devices()
        send_command("GET_GLOBAL")

    # --------- Global UI helpers ---------

    def apply_global_rules_to_ui(self):
        active = self.global_rules.get("active", False)
        mode_val = self.global_rules.get("mode", "any")
        action = self.global_rules.get("action", "lock")
        test = self.global_rules.get("test_mode", True)
        wipe_target = self.global_rules.get("wipe_target", "")
        custom_cmd = self.global_rules.get("custom_cmd", "")

        # Map daemon state → GUI state
        gui_mode = "off" if not active else mode_val
        if gui_mode not in self.GLOBAL_MODES:
            gui_mode = "off"

        # Avoid feedback loops
        self.global_mode_combo.blockSignals(True)
        self.global_action_combo.blockSignals(True)
        self.global_test_check.blockSignals(True)

        self.global_mode_combo.setCurrentText(gui_mode)
        if action not in self.GLOBAL_ACTIONS:
            action = "lock"
        self.global_action_combo.setCurrentText(action)
        self.global_test_check.setChecked(test)

        self.global_mode_combo.blockSignals(False)
        self.global_action_combo.blockSignals(False)
        self.global_test_check.blockSignals(False)

        # Update wipe target / custom command fields if they exist
        if hasattr(self, "global_wipe_target_edit"):
            self.global_wipe_target_edit.setText(wipe_target)
        if hasattr(self, "global_custom_cmd_edit"):
            self.global_custom_cmd_edit.setText(custom_cmd)

    def on_global_changed(self):
        """
        Called whenever the global mode / action / test checkbox changes
        or when the global wipe target / custom command fields are edited.

        We map GUI's "off" -> active=False and avoid sending 'off' as mode
        to the daemon, which expects insert/remove/any.
        """
        gui_mode = self.global_mode_combo.currentText()
        effective_mode = "any" if gui_mode == "off" else gui_mode

        wipe_target = ""
        if hasattr(self, "global_wipe_target_edit"):
            wipe_target = self.global_wipe_target_edit.text().strip()

        custom_cmd = ""
        if hasattr(self, "global_custom_cmd_edit"):
            custom_cmd = self.global_custom_cmd_edit.text().strip()

        self.global_rules = {
            "active": (gui_mode != "off"),
            "mode": effective_mode,
            "action": self.global_action_combo.currentText(),
            "custom_cmd": custom_cmd,
            "test_mode": self.global_test_check.isChecked(),
            "wipe_target": wipe_target,
        }

        payload = json.dumps(self.global_rules)
        send_command("SET_GLOBAL:" + payload)

    def auto_detect_global_wipe_target(self):
        """
        Auto-detect the physical device backing the root filesystem and
        put it into the global wipe target field.
        """
        try:
            src = subprocess.check_output(
                ["findmnt", "-no", "SOURCE", "/"],
                text=True
            ).strip()

            base = subprocess.check_output(
                ["lsblk", "-no", "PKNAME", src],
                text=True
            ).strip()

            if base:
                dev = f"/dev/{base}"
            else:
                dev = src

            if hasattr(self, "global_wipe_target_edit"):
                self.global_wipe_target_edit.setText(dev)
            # propagate change to daemon
            self.on_global_changed()
        except Exception as e:
            QMessageBox.warning(self, "Auto-detect failed", str(e))

    # --------- ARM / DISARM ---------

    def toggle_arm_state(self):
        if self.armed:
            self.disarm()
        else:
            self.arm()

    def arm(self):
        self.armed = True
        send_command("ARM")
        self.update_ui()

    def disarm(self):
        self.armed = False
        send_command("DISARM")
        self.update_ui()

    def update_ui(self):
        if self.armed:
            self.status_label.setText("Status: ARMED")
            self.toggle_button.setText("DISARM")
        else:
            self.status_label.setText("Status: DISARMED")
            self.toggle_button.setText("ARM")

        color = QColor(220, 20, 60) if self.armed else QColor(60, 179, 113)
        self.tray_icon.setIcon(make_circle_icon(color))

    # --------- USB Actions ---------

    def on_identify_usb(self):
        self.request_mode = "identify"
        send_command("LAST_EVENT")

    def on_register_usb(self):
        self.request_mode = "register"
        send_command("LAST_EVENT")

    def open_register_dialog_from_last_event(self):
        if not self.last_event:
            QMessageBox.information(
                self,
                "No event",
                "No last USB event available. Try again after inserting/removing a device.",
            )
            return

        base = {
            "id": str(uuid.uuid4()),
            "name": "New Duress USB",
            "vid": self.last_event.get("vid", ""),
            "pid": self.last_event.get("pid", ""),
            "serial": self.last_event.get("serial", ""),
            "wipe_target": "",
            "mode": "insert",
            "action": "wipe",
            "custom_cmd": "",
            "test_mode": True,
            "active": True,
        }

        dlg = DeviceDialog(base, self)
        if dlg.exec_() == QDialog.Accepted:
            new_dev = dlg.get_device()
            send_command("ADD_DEVICE:" + json.dumps(new_dev))
            self.request_devices()

    # --------- RESPONSE SOCKET ---------

    def setup_response_socket(self):
        try:
            if os.path.exists(SOCKET_GUI):
                os.remove(SOCKET_GUI)
            self.recv_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self.recv_socket.bind(SOCKET_GUI)
            # Allow root daemon to write back to GUI socket
            os.chmod(SOCKET_GUI, 0o666)
            self.recv_socket.setblocking(False)
            print(f"[GUI] Response socket bound at {SOCKET_GUI}")
        except OSError as e:
            print(f"[GUI] Failed to bind response socket: {e}")
            self.recv_socket = None
            return

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_daemon)
        self.poll_timer.start(200)

    def poll_daemon(self):
        if not self.recv_socket:
            return
        while True:
            try:
                data, _ = self.recv_socket.recvfrom(8192)
            except BlockingIOError:
                break
            except OSError:
                break
            if not data:
                break
            msg = data.decode(errors="ignore")
            print("[GUI] Received:", msg)
            self.handle_daemon_message(msg)

    def handle_daemon_message(self, msg: str):
        if msg.startswith("DEVICES:"):
            try:
                self.devices = json.loads(msg[len("DEVICES:"):])
                self.refresh_device_table()
            except Exception as e:
                print("[GUI] Failed to parse DEVICES payload:", e)
            return

        if msg.startswith("GLOBAL:"):
            try:
                self.global_rules = json.loads(msg[len("GLOBAL:"):])
                self.apply_global_rules_to_ui()
            except Exception as e:
                print("[GUI] Failed to parse GLOBAL payload:", e)
            return

        if msg.startswith("LAST_EVENT:"):
            payload = msg[len("LAST_EVENT:"):]
            try:
                event = json.loads(payload)
            except Exception:
                event = {}

            if event:
                self.last_event = event
                text = (
                    f"Last USB: action={event.get('action')} "
                    f"VID={event.get('vid')} PID={event.get('pid')} "
                    f"SERIAL={event.get('serial')}"
                )
                self.last_usb_label.setText(text)
                self.last_usb_label.setStyleSheet("color: black;")
            else:
                self.last_event = None
                self.last_usb_label.setText("Last USB event: (none)")
                self.last_usb_label.setStyleSheet("color: gray;")

            if self.request_mode == "identify":
                self.request_mode = None
            elif self.request_mode == "register":
                self.request_mode = None
                if not self.last_event:
                    QMessageBox.information(
                        self,
                        "No event",
                        "No recent USB event found. Insert/remove a device and try again.",
                    )
                else:
                    self.open_register_dialog_from_last_event()
            return

        if msg.startswith("OK:"):
            print("[GUI] Daemon OK:", msg)
            return

        if msg.startswith("ERROR:"):
            print("[GUI] Daemon ERROR:", msg)
            return

    # --------- DAEMON LOG SOCKET ---------

    def setup_log_socket(self):
        """
        Bind to SOCKET_LOG_GUI to receive daemon's forwarded print() lines.
        Daemon will sendto() this path. We use a non-blocking recv loop.
        """
        try:
            if os.path.exists(SOCKET_LOG_GUI):
                os.remove(SOCKET_LOG_GUI)
            self.log_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self.log_socket.bind(SOCKET_LOG_GUI)
            os.chmod(SOCKET_LOG_GUI, 0o666)
            self.log_socket.setblocking(False)
            print(f"[GUI] Log socket bound at {SOCKET_LOG_GUI}")
        except OSError as e:
            print(f"[GUI] Failed to bind log socket: {e}")
            self.log_socket = None
            return

        self.log_timer = QTimer(self)
        self.log_timer.timeout.connect(self.poll_daemon_logs)
        self.log_timer.start(200)

    def poll_daemon_logs(self):
        if not self.log_socket:
            return
        while True:
            try:
                data, _ = self.log_socket.recvfrom(8192)
            except BlockingIOError:
                break
            except OSError:
                break
            if not data:
                break
            line = data.decode(errors="ignore")
            # maintain circular buffer
            self.daemon_log_buffer.append(line)
            if len(self.daemon_log_buffer) > LOG_BUFFER_LIMIT:
                # drop oldest
                excess = len(self.daemon_log_buffer) - LOG_BUFFER_LIMIT
                if excess >= len(self.daemon_log_buffer):
                    self.daemon_log_buffer = []
                else:
                    self.daemon_log_buffer = self.daemon_log_buffer[excess:]
            # push to open window if exists
            if self.dev_window:
                self.dev_window.append_line(line)

    # --------- Device Table ---------

    def request_devices(self):
        send_command("GET_DEVICES")

    def refresh_device_table(self):
        self.device_table.blockSignals(True)
        self.device_table.clearContents()
        self.device_table.setRowCount(len(self.devices))

        for row, dev in enumerate(self.devices):
            # Active
            item = QTableWidgetItem()
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if dev.get("active") else Qt.Unchecked)
            self.device_table.setItem(row, 0, item)

            # Name
            self.device_table.setItem(
                row, 1, QTableWidgetItem(dev.get("name", dev.get("id", "")))
            )

            # Mode
            self.device_table.setItem(
                row, 2, QTableWidgetItem(dev.get("mode", "insert"))
            )

            # Action
            self.device_table.setItem(
                row, 3, QTableWidgetItem(dev.get("action", "wipe"))
            )

            # Test mode
            titem = QTableWidgetItem()
            titem.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            titem.setCheckState(Qt.Checked if dev.get("test_mode", True) else Qt.Unchecked)
            self.device_table.setItem(row, 4, titem)

        self.device_table.blockSignals(False)

    def get_selected_device(self):
        row = self.device_table.currentRow()
        if row < 0 or row >= len(self.devices):
            return None, row
        return self.devices[row], row

    def on_device_item_changed(self, item: QTableWidgetItem):
        row = item.row()
        col = item.column()
        if row < 0 or row >= len(self.devices):
            return

        dev = self.devices[row]

        if col == 0:
            dev["active"] = (item.checkState() == Qt.Checked)
        elif col == 4:
            dev["test_mode"] = (item.checkState() == Qt.Checked)
        else:
            return

        try:
            payload = json.dumps(dev)
            send_command("UPDATE_DEVICE:" + payload)
        except Exception as e:
            print("[GUI] Failed to serialize device for UPDATE_DEVICE:", e)

    def on_edit_device(self):
        dev, row = self.get_selected_device()
        if dev is None:
            QMessageBox.information(self, "No selection", "Select a device to edit.")
            return

        dlg = DeviceDialog(dev, self)
        if dlg.exec_() == QDialog.Accepted:
            updated = dlg.get_device()
            self.devices[row] = updated
            try:
                payload = json.dumps(updated)
                send_command("UPDATE_DEVICE:" + payload)
            except Exception as e:
                print("[GUI] Failed to serialize updated device:", e)
            self.refresh_device_table()

    def on_delete_device(self):
        dev, row = self.get_selected_device()
        if dev is None:
            QMessageBox.information(self, "No selection", "Select a device to delete.")
            return

        resp = QMessageBox.question(
            self,
            "Confirm deletion",
            f"Delete device '{dev.get('name', dev.get('id', 'Unnamed'))}'?",
        )
        if resp != QMessageBox.Yes:
            return

        dev_id = dev.get("id")
        if not dev_id:
            return

        send_command(f"DELETE_DEVICE:{dev_id}")
        self.request_devices()

    # --------- Dev window helpers ---------

    def open_dev_window(self):
        # create as top-level window (no parent)
        if self.dev_window is None:
            self.dev_window = DevLogWindow()
            # preload buffer
            for line in self.daemon_log_buffer:
                self.dev_window.append_line(line)
        # show top-level window; main GUI remains usable
        self.dev_window.show()
        self.dev_window.raise_()
        self.dev_window.activateWindow()

    # --------- Tray / Window helpers ---------

    def toggle_window_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.showNormal()
            self.activateWindow()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.toggle_window_visibility()

    def closeEvent(self, event):
        """
        Hide to tray instead of quitting when the window close button is pressed.
        """
        event.ignore()
        self.hide()

    def quit_app(self):
        self.tray_icon.hide()
        if self.recv_socket:
            try:
                self.recv_socket.close()
            except Exception:
                pass
            if os.path.exists(SOCKET_GUI):
                try:
                    os.remove(SOCKET_GUI)
                except Exception:
                    pass
        if self.log_socket:
            try:
                self.log_socket.close()
            except Exception:
                pass
            if os.path.exists(SOCKET_LOG_GUI):
                try:
                    os.remove(SOCKET_LOG_GUI)
                except Exception:
                    pass
        QApplication.quit()


def main():
    app = QApplication(sys.argv)
    win = DuressMainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
