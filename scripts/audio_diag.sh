#!/bin/bash
# audio_diag.sh — diagnose audio configuration for sound_mouth_sync
#
# Run this script on the robot to check audio device availability,
# PipeWire/PulseAudio status, and test audio capture.
#
# Usage:
#   ./audio_diag.sh              # full diagnostics
#   ./audio_diag.sh --test       # also do a 2-second capture test

set -u
RED='\033[0;31m'
GRN='\033[0;32m'
YLW='\033[0;33m'
NC='\033[0m'

ok()   { echo -e "  ${GRN}[OK]${NC} $*"; }
warn() { echo -e "  ${YLW}[WARN]${NC} $*"; }
fail() { echo -e "  ${RED}[FAIL]${NC} $*"; }

DO_TEST=false
[ "${1:-}" = "--test" ] && DO_TEST=true

echo "========================================"
echo " sound_mouth_sync Audio Diagnostics"
echo " $(date)"
echo "========================================"
echo ""

# --- 1. ALSA ---
echo "--- ALSA Devices ---"
if command -v aplay >/dev/null 2>&1; then
    USB_LINE=$(aplay -l 2>/dev/null | grep -i "USB Audio")
    if [ -n "$USB_LINE" ]; then
        ok "USB audio card found:"
        echo "      $USB_LINE"
        CARD_NUM=$(echo "$USB_LINE" | grep -oP 'card \K\d+')
        echo "      ALSA device: plughw:${CARD_NUM},0"
    else
        fail "No USB audio card found in 'aplay -l'"
    fi
    echo ""
    echo "  All playback devices:"
    aplay -l 2>/dev/null | sed 's/^/      /'
else
    fail "aplay not found (install alsa-utils)"
fi
echo ""

# --- 2. /proc/asound ---
echo "--- /proc/asound/cards ---"
if [ -f /proc/asound/cards ]; then
    cat /proc/asound/cards | sed 's/^/      /'
else
    warn "/proc/asound/cards not found"
fi
echo ""

# --- 3. PipeWire ---
echo "--- PipeWire ---"
PW_DAEMON=$(pgrep -x pipewire 2>/dev/null)
PW_PULSE=$(pgrep -f "pipewire-pulse" 2>/dev/null)
PW_SESSION=$(pgrep -f "pipewire-media-session|wireplumber" 2>/dev/null)

if [ -n "$PW_DAEMON" ]; then
    ok "pipewire daemon: PID $PW_DAEMON"
else
    fail "pipewire daemon NOT running"
fi

if [ -n "$PW_SESSION" ]; then
    ok "session manager: PID $PW_SESSION"
else
    warn "no session manager (pipewire-media-session / wireplumber)"
fi

if [ -n "$PW_PULSE" ]; then
    ok "pipewire-pulse: PID $PW_PULSE"
else
    warn "pipewire-pulse NOT running"
fi

if command -v pw-cli >/dev/null 2>&1; then
    PW_INFO=$(pw-cli info 0 2>&1 || true)
    if echo "$PW_INFO" | grep -q "type:"; then
        ok "pw-cli connected to PipeWire"
    else
        warn "pw-cli cannot connect: $PW_INFO"
    fi
fi
echo ""

# --- 4. PulseAudio / pactl ---
echo "--- PulseAudio (pactl) ---"
if command -v pactl >/dev/null 2>&1; then
    PA_SERVER=$(pactl info 2>&1 | grep "Server String" || echo "")
    if [ -n "$PA_SERVER" ]; then
        ok "pactl connected"
        echo "      $PA_SERVER"
        echo ""
        echo "  Default sink:"
        pactl get-default-sink 2>/dev/null | sed 's/^/      /' || echo "      (unknown)"
        echo ""
        echo "  Sinks:"
        pactl list sinks short 2>/dev/null | sed 's/^/      /' || echo "      (none)"
        echo ""
        echo "  Sources (look for .monitor):"
        pactl list sources short 2>/dev/null | sed 's/^/      /' || echo "      (none)"
        echo ""
        MONITOR=$(pactl list sources short 2>/dev/null | grep "\.monitor" | head -1 | awk '{print $2}')
        if [ -n "$MONITOR" ]; then
            ok "Monitor source found: $MONITOR"
        else
            warn "No .monitor source found — oscillogram capture may fail"
        fi
    else
        fail "pactl cannot connect (PulseAudio/pipewire-pulse not running?)"
        echo "      $(pactl info 2>&1 | head -3 | sed 's/^/      /')"
    fi
else
    warn "pactl not found"
fi
echo ""

# --- 5. Environment ---
echo "--- Environment ---"
echo "  UID: $(id -u) ($(whoami))"
echo "  XDG_RUNTIME_DIR: ${XDG_RUNTIME_DIR:-<not set>}"
echo "  PULSE_SERVER: ${PULSE_SERVER:-<not set>}"
echo "  PIPEWIRE_CONFIG_FILE: ${PIPEWIRE_CONFIG_FILE:-<not set>}"
RUNTIME="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
if [ -S "$RUNTIME/pipewire-0" ]; then
    ok "PipeWire socket exists: $RUNTIME/pipewire-0"
else
    warn "PipeWire socket NOT found at $RUNTIME/pipewire-0"
fi
if [ -S "$RUNTIME/pulse/native" ]; then
    ok "Pulse socket exists: $RUNTIME/pulse/native"
else
    warn "Pulse socket NOT found at $RUNTIME/pulse/native"
fi
echo ""

# --- 6. Capture test ---
if $DO_TEST; then
    echo "--- Capture Test (2 seconds) ---"
    echo "  Playing test tone in background..."

    ALSA_DEV="plughw:${CARD_NUM:-2},0"

    # Try to play a test tone
    if command -v speaker-test >/dev/null 2>&1; then
        speaker-test -D "$ALSA_DEV" -t sine -f 440 -l 1 -p 2 >/dev/null 2>&1 &
        PLAY_PID=$!
    elif command -v aplay >/dev/null 2>&1; then
        python3 -c "
import struct, math, sys
rate=16000; dur=2
for i in range(rate*dur):
    sys.stdout.buffer.write(struct.pack('<h', int(16000*math.sin(2*math.pi*440*i/rate))))
" | aplay -q -D "$ALSA_DEV" -f S16_LE -r 16000 -c 1 >/dev/null 2>&1 &
        PLAY_PID=$!
    else
        warn "No playback tool found, testing capture only"
        PLAY_PID=""
    fi

    sleep 0.5

    # Test parec capture
    if command -v parec >/dev/null 2>&1; then
        echo "  Testing parec capture..."
        CAPTURE=$(timeout 2 parec -r --raw --format=s16le --rate=16000 --channels=1 2>/dev/null | wc -c)
        if [ "$CAPTURE" -gt 1000 ]; then
            ok "parec captured $CAPTURE bytes"
        else
            fail "parec captured only $CAPTURE bytes (expected >1000)"
        fi
    fi

    # Test pw-record capture
    if command -v pw-record >/dev/null 2>&1; then
        echo "  Testing pw-record capture..."
        CAPTURE=$(timeout 2 pw-record -r --rate=16000 --channels=1 --format=s16 - 2>/dev/null | wc -c)
        if [ "$CAPTURE" -gt 1000 ]; then
            ok "pw-record captured $CAPTURE bytes"
        else
            fail "pw-record captured only $CAPTURE bytes (expected >1000)"
        fi
    fi

    [ -n "${PLAY_PID:-}" ] && kill "$PLAY_PID" 2>/dev/null || true
    wait 2>/dev/null
fi

echo "========================================"
echo " Diagnostics complete"
echo "========================================"
