#!/bin/bash

TARGET="$1"

if [ -z "$TARGET" ]; then
    echo "[wipe] ERROR: No target specified"
    exit 1
fi

if [ ! -b "$TARGET" ]; then
    echo "[wipe] ERROR: $TARGET is not a block device"
    exit 1
fi

echo "[wipe] Destroying LUKS header on $TARGET..."

# Irreversible LUKS header destruction
cryptsetup luksErase --batch-mode "$TARGET" 2>/dev/null

# Extra paranoia: clobber first 32KB
dd if=/dev/urandom of="$TARGET" bs=4096 count=8 status=none

sync
echo "[wipe] COMPLETE — rebooting now."
systemctl reboot -i

