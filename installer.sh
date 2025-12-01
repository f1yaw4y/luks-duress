#!/bin/bash
set -e

echo "=== Installing LUKS Duress ==="

# Root check
if [[ $EUID -ne 0 ]]; then
    echo "Please run as root: sudo ./install.sh"
    exit 1
fi

# ----------------------------
# 1. Install Dependencies
# ----------------------------
echo "Installing dependencies..."
apt update
apt install -y python3 python3-pip python3-pyudev python3-pyqt5 python3-dbus systemd

pip3 install pyudev || true

# ----------------------------
# 2. Install App Into /opt
# ----------------------------
INSTALL_DIR="/opt/luks-duress"
echo "Copying program files to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"

cp -r src "$INSTALL_DIR/"
cp -r config "$INSTALL_DIR/"
cp -r icons "$INSTALL_DIR/" 2>/dev/null || true

chmod +x "$INSTALL_DIR/src/daemon/duress_daemon.py"
chmod +x "$INSTALL_DIR/src/gui/duress_gui.py"
chmod +x "$INSTALL_DIR/src/daemon/helpers/lock-screen.sh"

# ----------------------------
# 3. Create Systemd Service
# ----------------------------
echo "Creating systemd service..."

cat <<EOF >/etc/systemd/system/luks-duress.service
[Unit]
Description=LUKS Duress USB Trigger Daemon
After=multi-user.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/luks-duress/src/daemon/duress_daemon.py
Restart=always
RestartSec=1
User=root

[Install]
WantedBy=multi-user.target
EOF

# Enable + start the service
systemctl daemon-reload
systemctl enable luks-duress.service
systemctl start luks-duress.service
echo "System service installed and running."

# ----------------------------
# 4. Create GUI Launcher Wrapper
# ----------------------------
echo "Creating launcher wrapper..."

cat <<EOF >/usr/local/bin/luks-duress
#!/bin/bash
# GUI launcher; daemon is now managed by systemd.
nohup python3 /opt/luks-duress/src/gui/duress_gui.py >/dev/null 2>&1 &
EOF

chmod +x /usr/local/bin/luks-duress

# ----------------------------
# 5. Desktop Entry
# ----------------------------
echo "Creating desktop entry..."

cat <<EOF >/usr/share/applications/luks-duress.desktop
[Desktop Entry]
Version=1.0
Type=Application
Name=LUKS Duress
Exec=luks-duress
Terminal=false
Icon=/opt/luks-duress/icons/icon.png
Comment=USB-trigger duress protection system
Categories=Security;
EOF

# ----------------------------
# 6. Autostart GUI
# ----------------------------
echo "Setting up autostart..."

AUTOSTART_DIR="/home/$SUDO_USER/.config/autostart"
mkdir -p "$AUTOSTART_DIR"

cat <<EOF >"$AUTOSTART_DIR/luks-duress.desktop"
[Desktop Entry]
Type=Application
Name=LUKS Duress
Exec=luks-duress
Terminal=false
Icon=/opt/luks-duress/icons/icon.png
EOF

chown $SUDO_USER:$SUDO_USER "$AUTOSTART_DIR/luks-duress.desktop"

echo "=== Installation complete! ==="
echo "Daemon status: sudo systemctl status luks-duress.service"
echo "Run GUI with: luks-duress"

