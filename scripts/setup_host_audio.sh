#!/bin/bash
# setup_host_audio.sh — redirect host audio to PulseAudio inside Docker container.
#
# Run this ON THE RASPBERRY PI HOST (not inside Docker).
# After running, all audio (VLC, aplay, TTS, etc.) will play through the
# PulseAudio server inside the Docker container, which owns the USB sound card.
# The oscillogram on the OLED display will react to this audio.
#
# Usage:
#   source setup_host_audio.sh          # sets env vars in current shell
#   source setup_host_audio.sh --check  # verify connectivity
#   source setup_host_audio.sh --permanent  # add to ~/.bashrc
#
# Requirements:
#   - Docker container with sound_mouth_sync must be running
#   - audio_capture_node must be started (it launches PulseAudio + TCP)
#   - pulseaudio-utils must be installed on the host (for pactl/paplay)

set -e

PA_PORT="${PA_PORT:-4713}"
PA_HOST="${PA_HOST:-127.0.0.1}"
PA_SERVER="tcp:${PA_HOST}:${PA_PORT}"

case "${1:-}" in
    --check)
        export PULSE_SERVER="$PA_SERVER"
        echo "PULSE_SERVER=$PULSE_SERVER"
        if pactl info >/dev/null 2>&1; then
            echo "[OK] Connected to PulseAudio server in Docker"
            pactl info 2>/dev/null | grep -E "Server Name|Default Sink"
            echo ""
            echo "Test playback:  paplay /usr/share/sounds/alsa/Front_Left.wav"
        else
            echo "[FAIL] Cannot connect to PulseAudio at $PA_SERVER"
            echo ""
            echo "Checklist:"
            echo "  1. Is the Docker container running?"
            echo "  2. Is audio_capture_node started? (rosnode list | grep audio)"
            echo "  3. Is port $PA_PORT exposed?  (ss -tlnp | grep $PA_PORT)"
            echo "  4. Is pulseaudio-utils installed?  (sudo apt install pulseaudio-utils)"
            exit 1
        fi
        ;;

    --permanent)
        LINE="export PULSE_SERVER=$PA_SERVER"
        if grep -qF "PULSE_SERVER" ~/.bashrc 2>/dev/null; then
            echo "PULSE_SERVER already in ~/.bashrc — updating"
            sed -i "s|^export PULSE_SERVER=.*|$LINE|" ~/.bashrc
        else
            echo "" >> ~/.bashrc
            echo "# Route audio to Docker PulseAudio (sound_mouth_sync)" >> ~/.bashrc
            echo "$LINE" >> ~/.bashrc
        fi
        export PULSE_SERVER="$PA_SERVER"
        echo "Added to ~/.bashrc: $LINE"
        echo "Run 'source ~/.bashrc' or open a new terminal."
        ;;

    *)
        export PULSE_SERVER="$PA_SERVER"
        echo "PULSE_SERVER=$PULSE_SERVER"
        echo ""
        echo "Audio will now play through the Docker container's PulseAudio."
        echo "Run with --check to verify, --permanent to persist in ~/.bashrc."
        ;;
esac
