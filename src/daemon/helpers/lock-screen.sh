#!/bin/bash

# Simple cross-DE lock helper.
# Must be run as the *desktop user* (not root).

# 1. Cinnamon (Linux Mint)
if command -v cinnamon-screensaver-command >/dev/null 2>&1; then
  cinnamon-screensaver-command --lock && exit 0
fi

# 2. GNOME / generic loginctl session lock
if command -v loginctl >/dev/null 2>&1; then
  loginctl lock-session && exit 0
fi

# 3. xdg-screensaver (XFCE, others)
if command -v xdg-screensaver >/dev/null 2>&1; then
  xdg-screensaver lock && exit 0
fi

# 4. dm-tool (LightDM)
if command -v dm-tool >/dev/null 2>&1; then
  dm-tool lock && exit 0
fi

echo "Lock screen failed: no known locker worked." >&2
exit 1
