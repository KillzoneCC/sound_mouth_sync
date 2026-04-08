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
import sys
import threading
import time

import numpy as np
import rospy
from std_msgs.msg import Float32, Float32MultiArray

_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)
import mouth_display_helpers as _mdh

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

_pa_tcp_ready = False  # set True after _pa_enable_tcp() succeeds


def _pa_env():
    """Return env dict with XDG_RUNTIME_DIR (and PULSE_SERVER once TCP is up)."""
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
    if _pa_tcp_ready:
        env.setdefault("PULSE_SERVER", "tcp:127.0.0.1:4713")
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


def _nuke_all_client_confs():
    """Remove default-server from ALL pulse client.conf files before starting PA.

    ANY default-server= line (global or per-user) prevents `pulseaudio --start`
    from launching a new daemon.  We neutralise every known location.
    After PA + TCP are up, _pa_write_client_conf() recreates a safe config.
    """
    paths = [
        "/etc/pulse/client.conf",
        os.path.expanduser("~/.config/pulse/client.conf"),
    ]
    for path in paths:
        try:
            if not os.path.exists(path):
                continue
            with open(path) as f:
                content = f.read()
            if "default-server" not in content:
                continue
            # Try to remove
            try:
                os.remove(path)
                rospy.logwarn("audio_capture: removed %s (had default-server)", path)
                continue
            except OSError:
                pass
            # Can't remove (permissions) — overwrite without default-server
            try:
                with open(path, "w") as f:
                    f.write("autospawn = yes\n")
                rospy.logwarn("audio_capture: cleared default-server from %s", path)
            except OSError as e:
                rospy.logerr("audio_capture: cannot fix %s: %s — PA may fail to start", path, e)
        except OSError:
            pass


def _pa_start():
    """Start the native PulseAudio daemon if not running."""
    _nuke_all_client_confs()

    if _pa_is_running():
        rospy.loginfo("audio_capture: PulseAudio already running")
        return True

    subprocess.call(["pkill", "-f", "pipewire-pulse"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.5)

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
    global _pa_tcp_ready
    ok, modules = _pa_run(["pactl", "list", "modules", "short"])
    if ok and "module-native-protocol-tcp" in modules:
        rospy.loginfo("audio_capture: PA TCP module already loaded")
        _pa_tcp_ready = True
        return True

    ok, _ = _pa_run([
        "pactl", "load-module", "module-native-protocol-tcp",
        "port=%d" % port, "auth-anonymous=1",
    ])
    if ok:
        rospy.loginfo("audio_capture: PA TCP access enabled on port %d (auth-anonymous)", port)
        _pa_tcp_ready = True
        return True

    rospy.logwarn("audio_capture: failed to load module-native-protocol-tcp — "
                  "host applications will not be able to play through this PA server")
    return False


def _pa_write_client_conf():
    """Make PA reachable for all users via TCP (port 4713).

    Strategy (never write default-server to global /etc/pulse/client.conf —
    that blocks PA autospawn on next boot):
      1. Write ~/.config/pulse/client.conf with default-server=tcp for the
         current user so that pactl/parec/aplay from other sessions still work.
      2. Set PULSE_SERVER env for our own subprocess calls (already in _pa_env).
    """
    home = os.path.expanduser("~")
    user_conf_dir = os.path.join(home, ".config", "pulse")
    user_conf = os.path.join(user_conf_dir, "client.conf")
    lines = [
        "default-server = tcp:127.0.0.1:4713",
        "autospawn = yes",
        "",
    ]
    try:
        os.makedirs(user_conf_dir, exist_ok=True)
        with open(user_conf, "w") as f:
            f.write("\n".join(lines))
        rospy.loginfo("audio_capture: wrote %s (tcp:127.0.0.1:4713)", user_conf)
    except OSError as e:
        rospy.logwarn("audio_capture: cannot write %s: %s", user_conf, e)


def _alsa_set_pulse_default():
    """Write /etc/asound.conf so that aplay/arecord and all ALSA apps route through PulseAudio.

    Without this, `aplay file.wav` goes directly to hw:X bypassing PulseAudio,
    so audio_capture_node never sees the sound on usb_output.monitor.

    We point ALSA at the TCP endpoint so any user (root, ubuntu, etc.)
    can play audio without needing access to another user's unix socket.
    """
    asound_conf = "/etc/asound.conf"
    content = (
        "# Auto-generated by audio_capture_node (sound_mouth_sync).\n"
        "# Routes all ALSA output through PulseAudio TCP so oscillogram captures everything.\n"
        "pcm.!default {\n"
        "    type pulse\n"
        "    server tcp:127.0.0.1:4713\n"
        "}\n"
        "ctl.!default {\n"
        "    type pulse\n"
        "    server tcp:127.0.0.1:4713\n"
        "}\n"
    )
    try:
        existing = ""
        if os.path.exists(asound_conf):
            with open(asound_conf) as f:
                existing = f.read()
        if "tcp:127.0.0.1:4713" in existing:
            rospy.loginfo("audio_capture: %s already routes ALSA→PA TCP", asound_conf)
            return
        with open(asound_conf, "w") as f:
            f.write(content)
        rospy.loginfo("audio_capture: wrote %s — ALSA default → PA TCP (aplay will show on oscillogram)", asound_conf)
    except OSError as e:
        rospy.logwarn("audio_capture: cannot write %s: %s — aplay won't route through PA", asound_conf, e)


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

    if not _mdh.wait_for_robot_standup("mouth_audio_capture_node"):
        if rospy.is_shutdown():
            raise rospy.ROSInterruptException()

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
    _alsa_set_pulse_default()

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
