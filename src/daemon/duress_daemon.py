#!/usr/bin/env python3
import pyudev
import json
import os
import sys
import socket
import threading
import subprocess
import builtins

# -------------------------------------------------
# LOG SOCKET (daemon sends to GUI bind path)
# -------------------------------------------------

SOCKET_LOG_GUI = "/tmp/luks-duress_log_gui"

def send_log_to_gui(line: str):
    """
    Send a log line to the GUI log socket. GUI binds to SOCKET_LOG_GUI.
    We create a transient AF_UNIX SOCK_DGRAM socket for each send to keep things simple.
    If GUI is not present, this will fail silently.
    """
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        # send to GUI socket path
        s.sendto(line.encode(), SOCKET_LOG_GUI)
        s.close()
    except Exception:
        # silent fail if gui not present or file removed
        pass

# Wrap built-in print so every print is also forwarded to GUI.
_real_print = builtins.print
def print(*args, **kwargs):
    text = " ".join(str(a) for a in args)
    # forward to GUI (best-effort)
    try:
        send_log_to_gui(text)
    except Exception:
        pass
    _real_print(*args, **kwargs)

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
            "test_mode": True,
            "wipe_target": ""
        }

    # Backfill missing wipe_target for older configs
    if "wipe_target" not in data["global_rules"]:
        data["global_rules"]["wipe_target"] = ""

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
# SOCKET HELPERS
# -------------------------------------------------


def init_socket():
    """
    Create and bind a Unix datagram socket for receiving commands.
    """
    if os.path.exists(SOCKET_CMD):
        try:
            os.remove(SOCKET_CMD)
        except OSError:
            pass

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(SOCKET_CMD)

    # Allow any user to write commands (GUI as user, daemon as root)
    os.chmod(SOCKET_CMD, 0o666)
    print(f"[Daemon] Command socket bound at {SOCKET_CMD}")
    return sock


def send_response(message: str):
    """
    Send a response back to the GUI via SOCKET_GUI (if it exists).
    """
    if not os.path.exists(SOCKET_GUI):
        # GUI not listening
        return
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.sendto(message.encode(), SOCKET_GUI)
        s.close()
    except OSError as e:
        print(f"[Daemon] Failed to send GUI response: {e}")


# -------------------------------------------------
# USB MONITORING
# -------------------------------------------------


def usb_monitor():
    """
    Monitor USB add/remove events via pyudev.
    """
    global last_usb_event

    context = pyudev.Context()
    monitor = pyudev.Monitor.from_netlink(context)
    monitor.filter_by(subsystem="usb")

    # REQUIRED in most pyudev versions
    monitor.enable_receiving()

    print("[Daemon] Starting USB monitor...")

    while True:
        dev = monitor.poll(timeout=None)  # blocks until event

        if dev is None:
            continue

        # Must check this because pyudev fires tons of sub-events
        if dev.get("DEVTYPE") != "usb_device":
            continue

        action = dev.action  # 'add' or 'remove'

        vid = (dev.get("ID_VENDOR_ID") or "").lower()
        pid = (dev.get("ID_MODEL_ID") or "").lower()
        serial = dev.get("ID_PATH") or dev.get("ID_SERIAL") or ""

        event_action = "insert" if action == "add" else "remove"

        last_usb_event = {
            "action": event_action,
            "vid": vid,
            "pid": pid,
            "serial": serial,
        }

        print(
            f"[Daemon] USB event: {event_action} "
            f"VID={vid} PID={pid} SERIAL={serial}"
        )

        # Hand back to existing logic
        handle_usb_event(
            event_action=event_action,
            vid=vid,
            pid=pid,
            serial=serial,
        )


def matching_devices(event_action, vid, pid, serial):
    """
    Return list of device rules that match this USB event.
    event_action: "insert" or "remove".
    """
    matches = []
    for dev in devices:
        if not dev.get("active", True):
            continue

        mode = dev.get("mode", "insert")
        if mode not in ("insert", "remove", "any"):
            mode = "insert"

        if mode != "any" and mode != event_action:
            continue

        # Match by VID/PID/SERIAL. We treat empty fields as wildcards.
        dvid = (dev.get("vid", "") or "").lower()
        dpid = (dev.get("pid", "") or "").lower()
        dser = dev.get("serial", "") or ""

        if dvid and dvid != vid:
            continue
        if dpid and dpid != pid:
            continue
        if dser and dser != serial:
            continue

        matches.append(dev)
    return matches


def check_global_rule(event_action):
    """
    Check if global rule matches this USB event.
    Returns the global_rules dict if it matches, else None.
    """
    if not global_rules.get("active", False):
        return None

    mode = global_rules.get("mode", "any")
    if mode not in ("insert", "remove", "any"):
        mode = "any"

    if mode != "any" and mode != event_action:
        return None

    return global_rules


def handle_usb_event(event_action, vid, pid, serial):
    """
    Called when a top-level USB device is added/removed.
    event_action: 'insert' or 'remove'
    """
    # 1) Global rule
    gl = check_global_rule(event_action)
    if gl:
        perform_action(gl, event_action, "GLOBAL")

    # 2) Per-device rules
    perdev = matching_devices(event_action, vid, pid, serial)
    for dev_entry in perdev:
        perform_action(dev_entry, event_action, dev_entry.get("name", "device"))


# -------------------------------------------------
# COMMAND HANDLING
# -------------------------------------------------


def handle_command(message: str):
    """
    Handle a single command string from the GUI.
    """
    global armed, devices, global_rules, last_usb_event

    # Simple state commands
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

    if message == "LAST_EVENT":
        payload = json.dumps(last_usb_event or {})
        send_response("LAST_EVENT:" + payload)
        return

    # More complex commands with payloads
    if message.startswith("SET_GLOBAL:"):
        payload = message[len("SET_GLOBAL:"):]
        try:
            new_gl = json.loads(payload)
        except Exception as e:
            print("[Daemon] Failed to parse SET_GLOBAL payload:", e)
            send_response("ERROR:BAD_GLOBAL_JSON")
            return

        # Ensure wipe_target always exists
        if "wipe_target" not in new_gl:
            new_gl["wipe_target"] = global_rules.get("wipe_target", "")

        global_rules = new_gl
        save_config(devices, global_rules)
        send_response("OK:GLOBAL_UPDATED")
        return

    if message.startswith("ADD_DEVICE:"):
        payload = message[len("ADD_DEVICE:"):]
        try:
            dev = json.loads(payload)
        except Exception as e:
            print("[Daemon] Failed to parse ADD_DEVICE payload:", e)
            send_response("ERROR:BAD_DEVICE_JSON")
            return

        # upsert by id
        dev_id = dev.get("id")
        if not dev_id:
            send_response("ERROR:MISSING_DEVICE_ID")
            return

        for i, d in enumerate(devices):
            if d.get("id") == dev_id:
                devices[i] = dev
                break
        else:
            devices.append(dev)

        save_config(devices, global_rules)
        send_response("OK:DEVICE_ADDED")
        return

    if message.startswith("UPDATE_DEVICE:"):
        payload = message[len("UPDATE_DEVICE:"):]
        try:
            dev = json.loads(payload)
        except Exception as e:
            print("[Daemon] Failed to parse UPDATE_DEVICE payload:", e)
            send_response("ERROR:BAD_DEVICE_JSON")
            return

        dev_id = dev.get("id")
        if not dev_id:
            send_response("ERROR:MISSING_DEVICE_ID")
            return

        for i, d in enumerate(devices):
            if d.get("id") == dev_id:
                devices[i] = dev
                save_config(devices, global_rules)
                send_response("OK:DEVICE_UPDATED")
                return

        send_response("ERROR:DEVICE_NOT_FOUND")
        return

    if message.startswith("DELETE_DEVICE:"):
        dev_id = message[len("DELETE_DEVICE:"):]
        before = len(devices)
        devices = [d for d in devices if d.get("id") != dev_id]
        if len(devices) != before:
            save_config(devices, global_rules)
            send_response("OK:DEVICE_DELETED")
        else:
            send_response("ERROR:DEVICE_NOT_FOUND")
        return

    print(f"[Daemon] Unknown command: {message}")


def socket_listener(sock):
    """
    Thread: listens for commands on SOCKET_CMD.
    """
    print("[Daemon] Socket listener running...")
    while True:
        try:
            data, _ = sock.recvfrom(4096)
        except OSError:
            break
        if not data:
            continue
        msg = data.decode(errors="ignore")
        handle_command(msg)


# -------------------------------------------------
# ACTION EXECUTION
# -------------------------------------------------


def perform_action(action_dict, event_action, name):
    global armed

    action = action_dict.get("action", "lock")
    test_mode = action_dict.get("test_mode", True)
    custom_cmd = action_dict.get("custom_cmd", "")

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
        wipe_target = action_dict.get("wipe_target", "").strip()
        if not wipe_target:
            print("[Daemon] WIPE REQUESTED but no 'wipe_target' specified in rule.")
            return
        perform_header_wipe(wipe_target)
        return

    if action == "command":
        if not custom_cmd:
            print("[Daemon] No custom command.")
            return
        subprocess.run(custom_cmd, shell=True)
        return


# -------------------------------------------------
# HELPERS (LOCK + WIPE)
# -------------------------------------------------


def run_lock_helper():
    helper = os.path.join(PROJECT_ROOT, "src/daemon/helpers/lock-screen.sh")
    print(f"[Daemon] Invoking lock helper: {helper}")
    try:
        subprocess.run(["bash", helper], check=False)
    except Exception as e:
        print("[Daemon] Lock helper error:", e)


def perform_header_wipe(target):
    helper = os.path.join(PROJECT_ROOT, "src/daemon/helpers/wipe-luks-header.sh")
    print(f"[Daemon] Invoking header wipe helper for {target}...")
    try:
        subprocess.run(["bash", helper, target], check=False)
    except Exception as e:
        print("[Daemon] Wipe helper error:", e)


# -------------------------------------------------
# MAIN
# -------------------------------------------------

if __name__ == "__main__":
    sock = init_socket()
    threading.Thread(target=socket_listener, args=(sock,), daemon=True).start()
    usb_monitor()
