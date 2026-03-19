#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audio_capture_node — captures ALL audio being played by the system (speakers)
and publishes a 128-point waveform for oscillogram rendering on the mouth display.

This node captures the system audio output (not a specific file) so that any
sound — TTS, music, effects — is visualised on the mouth display.

Capture methods (in priority order):
  1. PipeWire monitor (pw-record)    — default, captures speaker output
  2. PulseAudio monitor (parec)      — fallback
  3. ALSA (arecord)                  — microphone input

Subscriptions:  none
Publications:
  /mouth/audio_wave  (Float32MultiArray)  128 float values in -1..1
  /audio/level       (Float32)            RMS level 0..1 (compatibility)

Parameters:
  ~source        "pipewire_monitor" | "pulse_monitor" | "alsa" (default: pipewire_monitor)
  ~pulse_source  PulseAudio source name (auto-detect if empty)
  ~device        ALSA device for source:=alsa (default: plughw:2,0)
  ~rate          sample rate Hz (default: 16000)
  ~chunk_size    samples per chunk (default: 1024)
  ~wave_width    oscillogram width in points (default: 128, matches display)
"""
from __future__ import division

import os
import subprocess
import threading
import time

import rospy
from std_msgs.msg import Float32, Float32MultiArray

SAMPLE_BYTES = 2
WAVE_WIDTH = 128
PULSE_MONITOR_SOURCE = "@DEFAULT_SINK@.monitor"


def _ensure_pipewire_session():
    """Set up XDG_RUNTIME_DIR and start PipeWire/pipewire-pulse if needed."""
    uid = os.getuid()
    if not os.environ.get("XDG_RUNTIME_DIR"):
        run_user = "/run/user/{}".format(uid)
        if os.path.isdir(run_user):
            os.environ["XDG_RUNTIME_DIR"] = run_user
        else:
            runtime = "/tmp/runtime-{}".format(uid)
            try:
                os.makedirs(runtime, mode=0o700, exist_ok=True)
                os.environ["XDG_RUNTIME_DIR"] = runtime
            except OSError:
                pass
    env = os.environ.copy()
    try:
        subprocess.check_output(["pactl", "info"], stderr=subprocess.DEVNULL, timeout=2, env=env)
        return
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass

    config_paths = ["/etc/pipewire/pipewire.conf", "/usr/share/pipewire/pipewire.conf"]
    config_content = None
    for p in config_paths:
        try:
            with open(p) as f:
                config_content = f.read()
            break
        except IOError:
            continue
    if config_content:
        lines = []
        for line in config_content.splitlines():
            stripped = line.strip().lower()
            if "rtkit" in stripped and not stripped.startswith("#"):
                lines.append("# " + line)
            else:
                lines.append(line)
        try:
            import tempfile
            fd, tmp_conf = tempfile.mkstemp(prefix="pipewire-headless-", suffix=".conf")
            os.write(fd, ("\n".join(lines) + "\n").encode("utf-8"))
            os.close(fd)
            env["PIPEWIRE_CONFIG_FILE"] = tmp_conf
        except (IOError, OSError):
            pass

    runtime_dir = env.get("XDG_RUNTIME_DIR", "")
    socket_path = os.path.join(runtime_dir, "pipewire-0") if runtime_dir else ""
    pipewire_ready = socket_path and os.path.exists(socket_path)

    if not pipewire_ready and subprocess.call(
        ["pgrep", "-x", "pipewire"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ) != 0:
        try:
            subprocess.Popen(
                ["pipewire"], env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            for _ in range(15):
                time.sleep(0.3)
                if socket_path and os.path.exists(socket_path):
                    pipewire_ready = True
                    break
        except (FileNotFoundError, OSError) as e:
            rospy.logdebug("audio_capture: pipewire start: %s", e)

    pulse_exe = None
    for cmd in ["pipewire-pulse", "pipewire-pulseaudio"]:
        try:
            exe = subprocess.check_output(
                ["which", cmd], stderr=subprocess.DEVNULL, timeout=1, text=True
            ).strip()
            if exe:
                pulse_exe = exe
                break
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
    if pulse_exe:
        try:
            subprocess.Popen(
                [pulse_exe], env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            for _ in range(20):
                time.sleep(0.5)
                try:
                    subprocess.check_output(["pactl", "info"], stderr=subprocess.DEVNULL, timeout=1, env=env)
                    break
                except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    pass
        except (FileNotFoundError, OSError):
            pass


def _get_default_monitor_source():
    try:
        sink = subprocess.check_output(
            ["pactl", "get-default-sink"], stderr=subprocess.DEVNULL, timeout=3, text=True
        ).strip()
        if sink:
            return sink + ".monitor"
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    return None


def _find_analog_stereo_monitor():
    try:
        full = subprocess.check_output(
            ["pactl", "list", "sources"], stderr=subprocess.DEVNULL, timeout=5, text=True
        )
        in_source = False
        current_name = None
        for line in full.splitlines():
            line = line.strip()
            if line.startswith("Name:"):
                current_name = line.split(":", 1)[1].strip()
                in_source = True
            elif in_source and line.startswith("Description:"):
                desc = line.split(":", 1)[1].strip().lower()
                if "analog" in desc and "stereo" in desc and "output" in desc and current_name and current_name.endswith(".monitor"):
                    return current_name
                in_source = False
                current_name = None
            elif in_source and line and not line.startswith(" "):
                in_source = False
                current_name = None
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass

    try:
        out = subprocess.check_output(
            ["pactl", "list", "sources", "short"], stderr=subprocess.DEVNULL, timeout=5, text=True
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1].endswith(".monitor"):
            return parts[1]
    return None


def _compute_rms(data):
    n = len(data) // SAMPLE_BYTES
    if n == 0:
        return 0.0
    total = 0.0
    for i in range(n):
        j = i * SAMPLE_BYTES
        s = data[j] + (data[j + 1] << 8)
        if s >= 32768:
            s -= 65536
        total += s * s
    return (total / n) ** 0.5 / 32768.0


def _pcm_to_waveform(data, wave_width):
    """Convert raw PCM s16_le bytes to a list of `wave_width` float values in -1..1."""
    n_samples = len(data) // SAMPLE_BYTES
    if n_samples == 0:
        return [0.0] * wave_width
    samples = []
    for i in range(n_samples):
        j = i * SAMPLE_BYTES
        s = data[j] + (data[j + 1] << 8)
        if s >= 32768:
            s -= 65536
        samples.append(s / 32768.0)

    if n_samples <= wave_width:
        pad = [0.0] * (wave_width - n_samples)
        return pad + samples
    step = n_samples / float(wave_width)
    result = []
    for i in range(wave_width):
        idx = int(i * step)
        idx = min(idx, n_samples - 1)
        result.append(samples[idx])
    return result


def _start_arecord(device, rate):
    return subprocess.Popen(
        ["arecord", "-q", "-D", device, "-f", "S16_LE", "-r", str(rate), "-c", "1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )


def _start_parec(rate, source=None):
    src = source or PULSE_MONITOR_SOURCE
    return subprocess.Popen(
        ["parec", "-r", "-d", src, "--raw", "--format=s16le",
         "--rate=%d" % rate, "--channels=1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _start_pipewire_monitor(rate):
    return subprocess.Popen(
        ["pw-record", "-r", "--rate=%d" % rate, "--channels=1", "--format=s16", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=os.environ.copy(),
    )


def main():
    rospy.init_node("mouth_audio_capture_node", anonymous=False)

    source = str(rospy.get_param("~source", "pipewire_monitor")).strip().lower()
    device = str(rospy.get_param("~device", "plughw:2,0"))
    rate = int(rospy.get_param("~rate", 16000))
    chunk_size = int(rospy.get_param("~chunk_size", 1024))
    wave_width = int(rospy.get_param("~wave_width", WAVE_WIDTH))
    chunk_bytes = chunk_size * SAMPLE_BYTES

    if source in ("pulse_monitor", "pipewire_monitor"):
        _ensure_pipewire_session()

    pub_wave = rospy.Publisher("/mouth/audio_wave", Float32MultiArray, queue_size=5)
    pub_level = rospy.Publisher("/audio/level", Float32, queue_size=10)

    use_pulse = source == "pulse_monitor"
    use_pipewire = source == "pipewire_monitor"
    pulse_src = rospy.get_param("~pulse_source", "").strip()
    if use_pulse and not pulse_src:
        pulse_src = _find_analog_stereo_monitor() or _get_default_monitor_source() or PULSE_MONITOR_SOURCE
    elif use_pulse:
        pulse_src = pulse_src or PULSE_MONITOR_SOURCE

    proc = [None]
    shutdown = [False]
    pipewire_fail_count = [0]
    fallback_to_pulse = [False]
    fallback_pulse_src = [None]

    def capture_loop():
        while not shutdown[0] and not rospy.is_shutdown():
            if proc[0] is None:
                try:
                    if use_pipewire and not fallback_to_pulse[0]:
                        proc[0] = _start_pipewire_monitor(rate)
                    elif use_pulse or (use_pipewire and fallback_to_pulse[0]):
                        src = pulse_src if use_pulse else (fallback_pulse_src[0] or PULSE_MONITOR_SOURCE)
                        proc[0] = _start_parec(rate, src)
                    else:
                        proc[0] = _start_arecord(device, rate)
                except FileNotFoundError as e:
                    rospy.logerr_throttle(10.0, "audio_capture: %s", e)
                    rospy.sleep(2.0)
                    continue
                except Exception as e:
                    rospy.logerr_throttle(10.0, "audio_capture: %s", e)
                    rospy.sleep(2.0)
                    continue

            try:
                if proc[0].stdout is None:
                    proc[0] = None
                    continue
                data = proc[0].stdout.read(chunk_bytes)
                if not data or len(data) < chunk_bytes:
                    if proc[0].poll() is not None:
                        err_text = ""
                        try:
                            err = (proc[0].stderr and proc[0].stderr.read()) or b""
                            if err:
                                err_text = err.decode("utf-8", errors="replace").strip()
                                if use_pipewire and not fallback_to_pulse[0] and (
                                    "Broken pipe" in err_text or "connection error" in err_text
                                ):
                                    pipewire_fail_count[0] += 1
                                    if pipewire_fail_count[0] >= 2:
                                        try:
                                            subprocess.check_output(["which", "parec"], stderr=subprocess.DEVNULL, timeout=1)
                                            subprocess.check_output(["pactl", "info"], stderr=subprocess.DEVNULL, timeout=2)
                                            fallback_to_pulse[0] = True
                                            fallback_pulse_src[0] = (
                                                _get_default_monitor_source() or
                                                _find_analog_stereo_monitor() or
                                                PULSE_MONITOR_SOURCE
                                            )
                                            rospy.logwarn("audio_capture: falling back to pulse_monitor (parec)")
                                        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                                            pipewire_fail_count[0] = 0
                                else:
                                    rospy.logwarn_throttle(5.0, "audio_capture: process ended: %s", err_text or "(no output)")
                        except Exception:
                            pass
                        proc[0] = None
                        if use_pipewire and not fallback_to_pulse[0] and pipewire_fail_count[0] >= 1:
                            rospy.sleep(5.0)
                    continue

                level = _compute_rms(data)
                level = max(0.0, min(1.0, level)) if level == level else 0.0
                pub_level.publish(Float32(data=level))

                waveform = _pcm_to_waveform(data, wave_width)
                wave_msg = Float32MultiArray(data=waveform)
                pub_wave.publish(wave_msg)

            except Exception as e:
                rospy.logdebug("audio_capture read: %s", e)
                if proc[0] and proc[0].poll() is not None:
                    proc[0] = None

        if proc[0] and proc[0].poll() is None:
            try:
                proc[0].terminate()
            except Exception:
                pass
            proc[0] = None

    th = threading.Thread(target=capture_loop, daemon=True)
    th.start()

    source_label = {
        "pipewire_monitor": "speaker output (pw-record)",
        "pulse_monitor": "speaker output (parec)",
        "alsa": "microphone (ALSA %s)" % device,
    }.get(source, source)
    rospy.loginfo(
        "audio_capture_node: capturing %s, rate=%d, publishing /mouth/audio_wave (%d pts) + /audio/level",
        source_label, rate, wave_width,
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
