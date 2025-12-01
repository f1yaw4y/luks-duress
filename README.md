# LUKS-Duress

LUKS-Duress is an open-source Linux security tool that automatically performs duress actions (lock screen, shutdown, secure wipe, or custom commands) when specific USB events occur.
This project was inspired by BusKill but expands its capabilities to create a fully configurable, GUI-controlled, per-device or global USB-trigger system — ideal for privacy-focused users, sysadmins, journalists, pentesters, or anyone who needs a reliable duress response system on their Linux machines.

## Features

### Per-Device USB Triggers
- Register any USB device (by VID, PID, and Serial) and assign:
  - Trigger Mode:
    - insert (device added)
    - remove (device unplugged)
    - any (either event)
  - Action:
    - lock (locks the graphical session)
    - shutdown (clean system power-off)
    - wipe (LUKS header wipe stub, safe by default)
    - command (custom shell command)
  - Test Mode — simulate without performing the real action
  - Active/Inactive toggle per device

### Global USB Trigger Mode
A system-wide mode that responds to any USB being added, removed, or either.
This mode is active in addition to per-device rules, enabling combinations like:
- Global: “Lock on any USB removal”
- Device: “Shutdown if this specific USB is removed”

## GUI Control Panel (PyQt5)
The desktop application includes:
- Arm / Disarm system state
- Real-time status indicator
- USB device manager with edit/delete
- Auto-registration of last USB event
- Global rule configuration
- System tray icon (persists when UI is closed)
- Live daemon communication
- Last USB event viewer

## Robust Daemon
The daemon:
- Runs continuously in the background
- Monitors USB events using pyudev
- Responds to GUI commands via UNIX sockets
- Executes duress actions safely and predictably
- Handles multiple rule types (global + per-device)
- Installed as a systemd service

## Cross-Desktop Locking
The lock-screen.sh helper supports:
- Cinnamon  
- GNOME  
- KDE Plasma  
- XFCE  
- MATE  
- Unity  
- Deepin  
- i3 / sway (where possible)  
- X11 + Wayland fallbacks  
- xdg-screensaver, loginctl, qdbus, etc.

The script auto-detects the desktop environment and chooses the best locking method.


## Installation

### 1. Clone the Repository
```bash
git clone https://github.com/f1yaw4y/luks-duress.git
cd luks-duress
```

### 2. Launch the installer
```bash
sudo chmod +x installer.sh
./installer.sh
```

After installation the daemon will:
- Start at boot
- Run silently in the background
- Monitor USB events continuously

### 4. Run the application from your app launcher


## Project Structure
```
luks-duress/
│
├── src/
│   ├── daemon/
│   │   ├── duress_daemon.py
│   │   └── helpers/
│   │       └── lock-screen.sh
│   └── gui/
│       └── duress_gui.py
│
├── config/
│   └── rules.json
│
├── system/
│   └── luks-duress.service
│
├── LICENSE
│
└── README.md
```

## How It Works

### 1. Daemon Starts
- Watches /dev via pyudev.Monitor.from_netlink()
- Listens for commands from the GUI

### 2. USB Event Occurs
- Extracts VID/PID/Serial
- Saves event for “Identify USB” feature

### 3. Matching Logic
The daemon evaluates in order (this logic will be changed in the future):
1. Global Rule (if active)  
2. Per-Device Rules  
(Rules only trigger when Armed)

### 4. Action Executed
- Test mode outputs simulated action  
- Real mode performs lock/shutdown/command

### 5. GUI Updates Live
- Receives last event  
- Refreshes device list  
- Updates tray icon state

## Future Enhancements (Roadmap)
- Encrypted wipe target configuration  
- Notification popups for simulated actions  
- Export/import rule sets  
- Multi-socket architecture for multi-user desktops  
- Automatic packaging for .deb / .rpm / Flatpak  
- KDE systray theme integration  

## License
Creative Commons Attribution-NonCommercial 4.0 International License
