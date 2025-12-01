#!/usr/bin/env python3
import pyudev
import json
import os
import sys
import socket
import threading
import subprocess

# -------------------------------------------------
# CONFIG LOADING
# -------------------------------------------------

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
CONFIG = os.path.join(PROJECT_ROOT, "config", "rules.json")

def load_config():
    """
    Load devices + global_rules from config.
    """
    try:
        with open(CONFIG, "r") as f:
            data = json.load(f)
    except Exception as e:
        print("[Daemon] Error loading rules.json:", e)
        sys.exit(1)

    # Ensure structure exists
    if "devices" not in data:
        data["devices"] = []
    if "global_rules" not in data:
        data["global_rules"] = {
            "active": False,
            "mode": "any",
            "action": "lock",
            "custom_cmd": "",
            "test_mode": True
        }

    return data["devices"], data["global_rules"]

def save_config(devices, global_rules):
    """
    Save full config atomically.
    """
    tmp = CONFIG + ".tmp"
    data = {
        "devices": devices,
        "global_rules": global_rules
    }
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, CONFIG)
        print("[Daemon] Config saved.")
    except Exception as e:
        print("[Daemon] Error saving config:", e)

devices, global_rules = load_config()

# -------------------------------------------------
# RUNTIME STATE
# -------------------------------------------------

SOCKET_CMD = "/tmp/luks-duress.sock"
SOCKET_GUI = "/tmp/luks-duress_gui"

armed = False
last_usb_event = None

# -------------------------------------------------
# SOCKET SETUP
# -------------------------------------------------

def init_socket():
    if os.path.exists(SOCKET_CMD):
        os.remove(SOCKET_CMD)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(SOCKET_CMD)
    print("[Daemon] Command socket initialized.")
    return sock

def send_response(message: str):
    """
    Daemon → GUI responses (GUI listener optional).
    """
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.connect(SOCKET_GUI)
        s.send(message.encode())
        s.close()
    except:
        pass  # GUI listener may not be active yet.

# -------------------------------------------------
# COMMAND HANDLER
# -------------------------------------------------

def handle_command(message: str):
    global armed, devices, global_rules

    print(f"[Daemon] Received command: {message}")

    if message == "ARM":
        armed = True
        send_response("OK:ARMED")
        return

    if message == "DISARM":
        armed = False
        send_response("OK:DISARMED")
        return

    if message == "GET_DEVICES":
        send_response("DEVICES:" + json.dumps(devices))
        return

    if message == "GET_GLOBAL":
        send_response("GLOBAL:" + json.dumps(global_rules))
        return

    if message.startswith("SET_GLOBAL:"):
        json_str = message[len("SET_GLOBAL:"):]
        try:
            global_rules = json.loads(json_str)
            save_config(devices, global_rules)
            send_response("OK:GLOBAL_UPDATED")
        except:
            send_response("ERROR:BAD_GLOBAL_JSON")
        return

    if message.startswith("LAST_EVENT"):
        if last_usb_event:
            send_response("LAST_EVENT:" + json.dumps(last_usb_event))
        else:
            send_response("LAST_EVENT:{}")
        return

    if message.startswith("ADD_DEVICE:"):
        json_str = message[len("ADD_DEVICE:"):]
        try:
            dev = json.loads(json_str)
            devices.append(dev)
            save_config(devices, global_rules)
            send_response("OK:ADDED")
        except:
            send_response("ERROR:BAD_JSON")
        return

    if message.startswith("UPDATE_DEVICE:"):
        json_str = message[len("UPDATE_DEVICE:"):]
        try:
            updated = json.loads(json_str)
            uuid = updated.get("id")
            for i, d in enumerate(devices):
                if d["id"] == uuid:
                    devices[i] = updated
                    save_config(devices, global_rules)
                    send_response("OK:UPDATED")
                    return
            send_response("ERROR:UUID_NOT_FOUND")
        except:
            send_response("ERROR:BAD_JSON")
        return

    if message.startswith("DELETE_DEVICE:"):
        uuid = message.split(":",1)[1]
        new_list = [d for d in devices if d["id"] != uuid]
        if len(new_list) == len(devices):
            send_response("ERROR:UUID_NOT_FOUND")
        else:
            devices = new_list
            save_config(devices, global_rules)
            send_response("OK:DELETED")
        return

    if message.startswith("SET_ACTIVE:"):
        _, uuid, onoff = message.split(":")
        onoff = bool(int(onoff))
        for d in devices:
            if d["id"] == uuid:
                d["active"] = onoff
                save_config(devices, global_rules)
                send_response("OK:SET_ACTIVE")
                return
        send_response("ERROR:UUID_NOT_FOUND")
        return

    send_response("ERROR:UNKNOWN_COMMAND")

def socket_listener(sock):
    while True:
        data, _ = sock.recvfrom(4096)
        if data:
            handle_command(data.decode())

# -------------------------------------------------
# MATCHING LOGIC
# -------------------------------------------------

def matches_mode(mode, action):
    return (
        (mode == "insert" and action == "add") or
        (mode == "remove" and action == "remove") or
        (mode == "any" and action in ("add", "remove"))
    )

def matching_devices(event_action, vid, pid, serial):
    matched = []

    for d in devices:
        if not d.get("active", False):
            continue

        d_vid = d.get("vid")
        d_pid = d.get("pid")
        d_serial = d.get("serial")

        # INSERT must match everything
        if event_action == "add":
            if d_vid != vid or d_pid != pid:
                continue
            if d_serial != serial:
                continue

        # REMOVE should match *only serial* (VID/PID often differ)
        elif event_action == "remove":
            if d_serial != serial:
                continue

        # Mode check
        if matches_mode(d.get("mode", "insert"), event_action):
            matched.append(d)

    return matched
# -------------------------------------------------
# ACTION EXECUTION
# -------------------------------------------------

def perform_action(action_dict, event_action, name):
    global armed

    action = action_dict.get("action", "lock")
    test_mode = action_dict.get("test_mode", True)
    custom_cmd = action_dict.get("custom_cmd","")

    if not armed:
        print(f"[Daemon] Trigger matched but system DISARMED.")
        return

    print(f"[Daemon] Triggered '{name}' action: {action}")

    if test_mode:
        print("[Daemon] TEST MODE — action suppressed.")
        return

    if action == "lock":
        run_lock_helper()
        return

    if action == "shutdown":
        subprocess.run(["systemctl", "poweroff"])
        return

    if action == "wipe":
        print("[Daemon] WIPE REQUESTED — no wipe targets defined yet.")
        return

    if action == "command":
        if not custom_cmd:
            print("[Daemon] No custom command.")
            return
        subprocess.run(custom_cmd, shell=True)
        return

# -------------------------------------------------
# LOCK SCREEN HELPER
# -------------------------------------------------

def run_lock_helper():
    helper = os.path.join(PROJECT_ROOT, "src/daemon/helpers/lock-screen.sh")

    try:
        subprocess.run(["bash", helper], check=False)
    except Exception as e:
        print("[Daemon] Lock helper error:",e)

# -------------------------------------------------
# USB MONITOR
# -------------------------------------------------

def usb_monitor():
    global last_usb_event

    ctx = pyudev.Context()
    mon = pyudev.Monitor.from_netlink(ctx)
    mon.filter_by(subsystem='usb')

    print("[Daemon] Monitoring USB events...")

    for dev in iter(mon.poll, None):
        action = dev.action  # "add" / "remove"
        if action not in ("add", "remove"):
            continue

        # -----------------------------------------
        # FIX: Use device properties instead of attributes
        # These survive even during 'remove' events.
        # -----------------------------------------
        vid = dev.get("ID_VENDOR_ID")
        pid = dev.get("ID_MODEL_ID")
        serial = dev.get("ID_SERIAL_SHORT")

        # Ignore events that still have no identifiers
        if not (vid and pid and serial):
            # Removal events may be missing direct IDs;
            # Try resolving from parent device node.
            parent = dev.find_parent("usb", "usb_device")
            if parent:
                vid = vid or parent.get("ID_VENDOR_ID")
                pid = pid or parent.get("ID_MODEL_ID")
                serial = serial or parent.get("ID_SERIAL_SHORT")

        if not (vid and pid and serial):
            # Still nothing? Then skip — but now rarely happens.
            continue

        last_usb_event = {
            "action": action,
            "vid": vid,
            "pid": pid,
            "serial": serial
        }

        print(f"[USB] {action.upper()} VID={vid} PID={pid} SERIAL={serial}")

        # --- 1. GLOBAL RULES ---
        if global_rules.get("active", False):
            if matches_mode(global_rules.get("mode", "any"), action):
                perform_action(global_rules, action, "GLOBAL-RULE")
                

        # --- 2. PER-DEVICE RULES ---
        perdev = matching_devices(action, vid, pid, serial)
        for dev_entry in perdev:
            perform_action(dev_entry, action, dev_entry.get("name", "device"))


# -------------------------------------------------
# MAIN
# -------------------------------------------------

if __name__ == "__main__":
    sock = init_socket()
    threading.Thread(target=socket_listener, args=(sock,), daemon=True).start()
    usb_monitor()
