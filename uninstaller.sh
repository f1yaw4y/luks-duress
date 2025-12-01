#!/bin/bash
set -e

echo "=== Uninstalling LUKS Duress ==="

if [[ $EUID -ne 0 ]]; then
    echo "Please run as root: sudo ./uninstall.sh"
    exit 1
fi

SERVICE_FILE="/etc/systemd/system/luks-duress.service"
INSTALL_DIR="/opt/luks-duress"
LAUNCHER="/usr/local/bin/luks-duress"
DESKTOP_FILE="/usr/share/applications/luks-duress.desktop"

# Detect user for autostart removal
TARGET_USER=${SUDO_USER:-$(logname)}
AUTOSTART_FILE="/home/$TARGET_USER/.config/autostart/luks-duress.desktop"

echo "Stopping service (if running)..."
systemctl stop luks-duress.service 2>/dev/null || true

echo "Disabling service..."
systemctl disable luks-duress.service 2>/dev/null || true

echo "Removing service file..."
rm -f "$SERVICE_FILE"
systemctl daemon-reload

echo "Removing /opt installation..."
rm -rf "$INSTALL_DIR"

echo "Removing launcher binary..."
rm -f "$LAUNCHER"

echo "Removing desktop entry..."
rm -f "$DESKTOP_FILE"

echo "Removing autostart entry..."
rm -f "$AUTOSTART_FILE"

echo "Cleaning leftover socket files..."
rm -f /tmp/luks-duress.sock
rm -f /tmp/luks-duress_gui

echo "=== LUKS Duress has been successfully uninstalled ==="
echo "If you want to remove Python dependencies, you may do so manually."

