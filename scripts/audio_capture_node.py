#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audio_capture_node — captures ALL system audio output and publishes a 128-point
waveform for oscillogram rendering on the mouth display.

Strategy:
  1. Ensure PulseAudio is running (native, NOT PipeWire-Pulse).
  2. Create a PulseAudio ALSA sink pointing at the USB sound card.
  3. Enable TCP access (port 4713) so host-side apps can play through this PA.
  4. Capture from that sink's ``.monitor`` source via ``parec``.

This guarantees we capture everything played through PulseAudio, regardless of
which program produces the sound (TTS, music, ROS nodes, etc.).

Publications:
  /mouth/audio_wave  (Float32MultiArray)  128 float values in -1..1
  /audio/level       (Float32)            RMS level 0..1

Parameters:
  ~rate          sample rate Hz (default: 48000)
  ~chunk_size    samples per chunk (default: 1024)
  ~wave_width    oscillogram width in points (default: 128)
"""
from __future__ import division

import os
import re
import subprocess
import threading
import time

import numpy as np
import rospy
from std_msgs.msg import Float32, Float32MultiArray

WAVE_WIDTH = 128
SAMPLE_BYTES = 2
PA_SINK_NAME = "usb_output"

# ---------------------------------------------------------------------------
# ALSA helpers
# ---------------------------------------------------------------------------

def _find_usb_alsa_card():
    """Return (card_num, card_name) for the first USB ALSA card, or (None, None)."""
    try:
        with open("/proc/asound/cards") as f:
            text = f.read()
    except IOError:
        return None, None
    for m in re.finditer(r"^\s*(\d+)\s+\[(\w+)\s*\].*USB", text, re.MULTILINE | re.IGNORECASE):
        return int(m.group(1)), m.group(2).strip()
    return None, None

# ---------------------------------------------------------------------------
# PulseAudio bootstrap
# ---------------------------------------------------------------------------

def _pa_env():
    """Return env dict with XDG_RUNTIME_DIR for PulseAudio."""
    env = os.environ.copy()
    if not env.get("XDG_RUNTIME_DIR"):
        uid = os.getuid()
        for candidate in ["/run/user/{}".format(uid), "/tmp/pulse-runtime-{}".format(uid)]:
            if os.path.isdir(candidate):
                env["XDG_RUNTIME_DIR"] = candidate
                break
        else:
            d = "/tmp/pulse-runtime-{}".format(uid)
            os.makedirs(d, mode=0o700, exist_ok=True)
            env["XDG_RUNTIME_DIR"] = d
    return env


def _pa_run(args, timeout=5):
    """Run a pactl command and return (success, stdout)."""
    env = _pa_env()
    try:
        out = subprocess.check_output(
            args, stderr=subprocess.DEVNULL, timeout=timeout, text=True, env=env
        )
        return True, out.strip()
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False, ""


def _pa_is_running():
    ok, _ = _pa_run(["pactl", "info"])
    return ok


def _pa_remove_stale_client_conf():
    """Remove /etc/pulse/client.conf if it points to a non-existent socket.

    A previous run may have written a client.conf with default-server pointing
    at a socket for a different uid.  Combined with autospawn=no this prevents
    PulseAudio from starting on the next boot.
    """
    _client_conf = "/etc/pulse/client.conf"
    try:
        if not os.path.exists(_client_conf):
            return
        with open(_client_conf) as f:
            content = f.read()
        m = re.search(r"default-server\s*=\s*unix:(.+)", content)
        if m:
            sock = m.group(1).strip()
            if not os.path.exists(sock):
                os.remove(_client_conf)
                rospy.logwarn("audio_capture: removed stale %s (socket %s missing)",
                              _client_conf, sock)
    except OSError:
        pass


def _pa_start():
    """Start the native PulseAudio daemon if not running."""
    _pa_remove_stale_client_conf()

    if _pa_is_running():
        rospy.loginfo("audio_capture: PulseAudio already running")
        return True

    subprocess.call(["pkill", "-f", "pipewire-pulse"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.5)

    _client_conf = "/etc/pulse/client.conf"
    _client_bak = _client_conf + ".bak"
    try:
        if os.path.exists(_client_conf):
            os.rename(_client_conf, _client_bak)
    except OSError:
        pass

    env = _pa_env()
    try:
        subprocess.Popen(
            ["pulseaudio", "--start", "--exit-idle-time=-1"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        rospy.logerr("audio_capture: pulseaudio binary not found")
        return False

    for _ in range(20):
        time.sleep(0.5)
        if _pa_is_running():
            rospy.loginfo("audio_capture: PulseAudio started")
            return True

    rospy.logerr("audio_capture: PulseAudio did not start within 10 seconds")
    return False


def _pa_ensure_usb_sink():
    """Load module-alsa-sink for the USB card if not already loaded. Return monitor source name."""
    card_num, card_name = _find_usb_alsa_card()
    if card_num is None:
        rospy.logerr("audio_capture: no USB ALSA card found in /proc/asound/cards")
        return None

    alsa_device = "plughw:{},0".format(card_num)
    monitor_src = PA_SINK_NAME + ".monitor"

    # Check if sink already exists
    ok, sinks = _pa_run(["pactl", "list", "sinks", "short"])
    if ok and PA_SINK_NAME in sinks:
        rospy.loginfo("audio_capture: PA sink '%s' already loaded (card %d [%s])",
                      PA_SINK_NAME, card_num, card_name)
        _pa_set_default(PA_SINK_NAME)
        return monitor_src

    # Load the module
    ok, mod_id = _pa_run([
        "pactl", "load-module", "module-alsa-sink",
        "device=" + alsa_device,
        "sink_name=" + PA_SINK_NAME,
        'sink_properties=device.description="USB-Audio"',
    ])
    if not ok:
        rospy.logerr("audio_capture: failed to load module-alsa-sink for %s", alsa_device)
        return None

    rospy.loginfo("audio_capture: loaded PA sink '%s' → %s (module %s, card %d [%s])",
                  PA_SINK_NAME, alsa_device, mod_id, card_num, card_name)
    _pa_set_default(PA_SINK_NAME)
    return monitor_src


def _pa_set_default(sink_name):
    ok, _ = _pa_run(["pactl", "set-default-sink", sink_name])
    if ok:
        rospy.loginfo("audio_capture: default PA sink → %s", sink_name)


def _pa_enable_tcp(port=4713):
    """Load module-native-protocol-tcp so host applications can connect."""
    ok, modules = _pa_run(["pactl", "list", "modules", "short"])
    if ok and "module-native-protocol-tcp" in modules:
        rospy.loginfo("audio_capture: PA TCP module already loaded")
        return True

    ok, _ = _pa_run([
        "pactl", "load-module", "module-native-protocol-tcp",
        "port=%d" % port, "auth-anonymous=1",
    ])
    if ok:
        rospy.loginfo("audio_capture: PA TCP access enabled on port %d (auth-anonymous)", port)
        return True

    rospy.logwarn("audio_capture: failed to load module-native-protocol-tcp — "
                  "host applications will not be able to play through this PA server")
    return False


def _pa_write_client_conf():
    """Write /etc/pulse/client.conf so any user in the container can reach PA.

    Always prefer TCP fallback so that the conf survives reboots regardless of
    which uid starts the daemon.  The unix socket path is uid-specific and may
    become stale after reboot.
    """
    conf_dir = "/etc/pulse"
    conf_path = os.path.join(conf_dir, "client.conf")

    env = _pa_env()
    xdg = env.get("XDG_RUNTIME_DIR", "")
    socket_path = os.path.join(xdg, "pulse", "native") if xdg else ""

    if socket_path and os.path.exists(socket_path):
        server_line = "default-server = unix:{} tcp:127.0.0.1:4713".format(socket_path)
    else:
        server_line = "default-server = tcp:127.0.0.1:4713"

    lines = [server_line, "autospawn = yes", ""]

    try:
        os.makedirs(conf_dir, exist_ok=True)
        with open(conf_path, "w") as f:
            f.write("\n".join(lines))
        if socket_path and os.path.exists(socket_path):
            try:
                os.chmod(os.path.dirname(socket_path), 0o755)
            except OSError:
                pass
        rospy.loginfo("audio_capture: wrote %s → %s", conf_path, server_line)
    except OSError as e:
        rospy.logwarn("audio_capture: cannot write %s: %s", conf_path, e)


# ---------------------------------------------------------------------------
# Waveform helpers
# ---------------------------------------------------------------------------

def _downsample_to_waveform(samples_f32, wave_width):
    n = len(samples_f32)
    if n == 0:
        return [0.0] * wave_width
    if n <= wave_width:
        return [0.0] * (wave_width - n) + samples_f32.tolist()
    indices = np.minimum((np.arange(wave_width) * (n / float(wave_width))).astype(int), n - 1)
    return samples_f32[indices].tolist()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    rospy.init_node("mouth_audio_capture_node", anonymous=False)

    rate = int(rospy.get_param("~rate", 48000))
    chunk_size = int(rospy.get_param("~chunk_size", 1024))
    wave_width = int(rospy.get_param("~wave_width", WAVE_WIDTH))
    chunk_bytes = chunk_size * SAMPLE_BYTES

    # --- bootstrap PulseAudio ---
    if not _pa_start():
        rospy.signal_shutdown("Cannot start PulseAudio")
        return

    monitor_src = _pa_ensure_usb_sink()
    if not monitor_src:
        rospy.signal_shutdown("Cannot create USB sink in PulseAudio")
        return

    _pa_enable_tcp()
    _pa_write_client_conf()

    pub_wave = rospy.Publisher("/mouth/audio_wave", Float32MultiArray, queue_size=5)
    pub_level = rospy.Publisher("/audio/level", Float32, queue_size=10)

    shutdown = [False]
    silence_chunks = [0]
    proc = [None]

    def _start_parec():
        env = _pa_env()
        return subprocess.Popen(
            ["parec", "-d", monitor_src, "--raw",
             "--format=s16le", "--rate=%d" % rate, "--channels=1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )

    def capture_loop():
        while not shutdown[0] and not rospy.is_shutdown():
            if proc[0] is None:
                try:
                    proc[0] = _start_parec()
                    rospy.loginfo_once("audio_capture: parec started (source=%s, rate=%d)",
                                       monitor_src, rate)
                except FileNotFoundError:
                    rospy.logerr_throttle(10.0, "audio_capture: parec not found")
                    rospy.sleep(2.0)
                    continue
                except Exception as e:
                    rospy.logerr_throttle(10.0, "audio_capture: start error: %s", e)
                    rospy.sleep(2.0)
                    continue

            try:
                data = proc[0].stdout.read(chunk_bytes)
                if not data or len(data) < chunk_bytes:
                    if proc[0].poll() is not None:
                        err = b""
                        try:
                            err = (proc[0].stderr and proc[0].stderr.read()) or b""
                        except Exception:
                            pass
                        rospy.logwarn_throttle(5.0, "audio_capture: parec exited: %s",
                                              err.decode("utf-8", errors="replace").strip() or "(no output)")
                        proc[0] = None
                        rospy.sleep(1.0)
                    continue

                buf = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                rms_raw = float(np.sqrt(np.mean(buf * buf)))
                level = max(0.0, min(1.0, rms_raw / 32768.0))
                pub_level.publish(Float32(data=level))

                waveform = _downsample_to_waveform(buf / 32768.0, wave_width)
                pub_wave.publish(Float32MultiArray(data=waveform))

                if level < 0.001:
                    silence_chunks[0] += 1
                    if silence_chunks[0] == 200:
                        rospy.logwarn(
                            "audio_capture: %d consecutive silent chunks — "
                            "is anything playing through PulseAudio?", silence_chunks[0])
                else:
                    silence_chunks[0] = 0

            except Exception as e:
                rospy.logdebug("audio_capture read: %s", e)
                if proc[0] and proc[0].poll() is not None:
                    proc[0] = None

        if proc[0] and proc[0].poll() is None:
            try:
                proc[0].terminate()
            except Exception:
                pass

    th = threading.Thread(target=capture_loop, daemon=True)
    th.start()

    rospy.loginfo(
        "audio_capture_node: capturing PA monitor '%s' @ %d Hz, "
        "publishing /mouth/audio_wave (%d pts) + /audio/level",
        monitor_src, rate, wave_width,
    )
    rospy.spin()
    shutdown[0] = True
    th.join(timeout=2.0)


if __name__ == "__main__":
    try:
        main()
    except rospy.ROSInterruptException:
        pass
    except Exception as e:
        rospy.logerr("audio_capture_node: %s", e)
        raise
