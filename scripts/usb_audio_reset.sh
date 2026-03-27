#!/bin/bash
# usb_audio_reset.sh — reset USB audio device after reboot
#
# On Raspberry Pi, USB audio cards sometimes fail to initialize at boot.
# This script finds the USB audio device and performs a soft reset by
# toggling its "authorized" flag in sysfs.
#
# Usage:
#   sudo ./usb_audio_reset.sh          # auto-detect USB audio device
#   sudo ./usb_audio_reset.sh 1-1.4    # specify USB device path

set -e

USB_PATH="${1:-}"

find_usb_audio_device() {
    for card_dir in /proc/asound/card*/; do
        [ -d "$card_dir" ] || continue
        local usbid_file="${card_dir}usbid"
        [ -f "$usbid_file" ] || continue

        local card_num
        card_num=$(basename "$card_dir" | sed 's/card//')
        local card_name
        card_name=$(cat "/proc/asound/card${card_num}/id" 2>/dev/null || echo "unknown")

        for dev in /sys/bus/usb/devices/*/sound/card${card_num}; do
            [ -d "$dev" ] || continue
            local usb_dev
            usb_dev=$(echo "$dev" | sed 's|/sys/bus/usb/devices/||;s|/sound/.*||')
            echo "$usb_dev"
            return 0
        done
    done
    return 1
}

if [ -z "$USB_PATH" ]; then
    USB_PATH=$(find_usb_audio_device)
    if [ -z "$USB_PATH" ]; then
        echo "ERROR: No USB audio device found. Is it plugged in?"
        exit 1
    fi
fi

AUTH_FILE="/sys/bus/usb/devices/${USB_PATH}/authorized"
if [ ! -f "$AUTH_FILE" ]; then
    echo "ERROR: sysfs path not found: $AUTH_FILE"
    echo "Available USB devices:"
    ls /sys/bus/usb/devices/ 2>/dev/null | grep -v ':'
    exit 1
fi

CARD_INFO=$(cat /proc/asound/cards 2>/dev/null | grep -i usb || echo "(unknown)")
echo "USB audio device: $USB_PATH"
echo "ALSA info: $CARD_INFO"
echo "Resetting..."

echo 0 > "$AUTH_FILE"
sleep 1
echo 1 > "$AUTH_FILE"
sleep 2

if aplay -l 2>/dev/null | grep -qi "USB Audio"; then
    echo "OK: USB audio device is back."
    aplay -l 2>/dev/null | grep -i "USB Audio"
else
    echo "WARNING: USB audio device not detected after reset."
    echo "Try physically unplugging and re-plugging the device."
    exit 1
fi
