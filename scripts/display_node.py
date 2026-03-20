#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
display_node — manages the OLED SSD1306 128x64 (I2C 0x3D) mounted on the robot's
head (mouth area). Two display modes:

  emotion      — static or animated mouth expression (happy, sad, neutral, ...)
  oscillogram  — real-time audio waveform received from audio_capture_node

Subscriptions:
  /mouth/mode        (String)            "emotion" | "oscillogram"
  /mouth/emotion     (String)            emotion name
  /mouth/audio_wave  (Float32MultiArray) 128 values in -1..1
  /audio/level       (Float32)           chunk RMS 0..1 (audio_capture) — extra trigger for oscillogram
  /robot/posture     (String)            stand | fall_* (from joystick_control)
  /robot/is_moving   (Bool)              gait moving (from joystick_control)

Publications:
  /mouth/current_mode    (String, latch)
  /mouth/current_emotion (String, latch)

Parameters:
  ~default_emotion         initial emotion (default: neutral)
  ~auto_mode               auto-switch to oscillogram on sound, back on silence (default: true)
  ~silence_return_sec      seconds of silence before returning to emotion mode (default: 3.0)
  ~idle_sleep_enabled     enable sleepy face after idle (default: true)
  ~idle_sleep_sec         idle timeout seconds (default: 60)
  ~idle_sleep_emotion     emotion name for idle (default: sleepy)
  ~fall_emotion           emotion when fallen (default: angry)
  ~posture_topic           remapped /robot/posture
  ~movement_topic          remapped /robot/is_moving
  ~idle_require_movement_signal  require /robot/is_moving before idle sleep (default: true)
  ~audio_level_topic             subscribe for /audio/level (default from YAML)
  ~i2c_port                I2C port number (default: 1)
  ~i2c_address             I2C address (default: 0x3D = 61)
  ~width                   display width  (default: 128)
  ~height                  display height (default: 64)
"""
from __future__ import annotations

import atexit
import math
import os
import random
import sys
import time

import rospy
from std_msgs.msg import String, Float32MultiArray, Bool, Float32

try:
    import rospkg
    _pkg_path = rospkg.RosPack().get_path("sound_mouth_sync")
    sys.path.insert(0, os.path.join(_pkg_path, "scripts"))
except Exception:
    pass
# Always include this file's directory (devel/install: deps next to the node script).
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

import sms_config
import mouth_audio_gates as _mouth_audio_gates

try:
    from luma.core.interface.serial import i2c
    from luma.oled.device import ssd1306
    from PIL import Image, ImageDraw, ImageFont
    _LUMA_AVAILABLE = True
except ImportError:
    _LUMA_AVAILABLE = False
    i2c = None
    ssd1306 = None
    Image = None
    ImageDraw = None

VALID_EMOTIONS = (
    "neutral", "happy", "sad", "angry", "surprised", "excited",
    "sleepy", "love", "confused", "scared", "bored", "calm",
    "disgusted", "tired",
)

# Waveform + /audio/level thresholds live in mouth_audio_gates.py (imported for tests).

_FALL_POSTURES = frozenset(
    ("fall_forward", "fall_backward", "fall_left", "fall_right"),
)

# Cached PIL fonts for sleepy Zzz (size -> ImageFont)
_SLEEPY_FONT_CACHE = {}


def _sleepy_font(size_px):
    """Bitmap font for floating Zzz; DejaVu if present, else default."""
    size_px = max(6, min(22, int(size_px)))
    if size_px not in _SLEEPY_FONT_CACHE:
        try:
            _SLEEPY_FONT_CACHE[size_px] = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size_px)
        except OSError:
            try:
                _SLEEPY_FONT_CACHE[size_px] = ImageFont.truetype(
                    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", size_px)
            except OSError:
                _SLEEPY_FONT_CACHE[size_px] = ImageFont.load_default()
    return _SLEEPY_FONT_CACHE[size_px]


def _sleepy_anim_reset(state):
    """Reset Zzz particle state when entering sleepy animation."""
    state.clear()
    state["start"] = time.time()
    state["zzz"] = []
    state["spawn_elapsed"] = 0.0


def _draw_sleepy_animated(width, height, state):
    """
    Breathing mouth ellipse + floating Z/z (1-bit OLED, PIL).
    Call repeatedly (~8 Hz) while showing sleepy.
    """
    if "start" not in state:
        _sleepy_anim_reset(state)
    img = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(img)
    cx, cy = width // 2, height // 2
    mouth_w = 36
    t0 = state["start"]
    elapsed = time.time() - t0

    # Spawn Z every ~1.5 s (in animation time)
    if elapsed - state["spawn_elapsed"] > 1.5:
        state["zzz"].append({
            "x": float(cx + 14),
            "y": float(cy - 10),
            "size": 12.0,
            "alpha": 255,
            "char": random.choice(("Z", "z", "Z")),
        })
        state["spawn_elapsed"] = elapsed

    # Update particles
    alive = []
    for z in state["zzz"]:
        z["x"] -= 0.8
        z["y"] -= 0.4
        z["size"] *= 0.98
        z["alpha"] -= 4
        if z["alpha"] > 0 and z["size"] >= 3.0:
            alive.append(z)
    state["zzz"] = alive

    # Breathing mouth (horizontal ellipse)
    breath = math.sin((time.time() - t0) * 1.2)
    mouth_h = max(4, int(5 + breath * 5))
    x0 = cx - mouth_w // 2
    y0 = cy - mouth_h // 2
    x1 = cx + mouth_w // 2
    y1 = cy + mouth_h // 2
    draw.ellipse((x0, y0, x1, y1), outline=255, width=2)

    # Zzz (skip draw when alpha low — fake fade on 1-bit)
    for z in state["zzz"]:
        if z["alpha"] < 40:
            continue
        font = _sleepy_font(z["size"])
        draw.text((int(z["x"]), int(z["y"])), z["char"], fill=255, font=font)

    return img


def _draw_emotion_angry_fall(width, height):
    """
    Angry mouth for fall state: lips + sharp teeth + scratch marks (1-bit).
    Coordinates adapted from pygame example to OLED center.
    """
    img = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(img)
    cx, cy = width // 2, height // 2
    mx, my = cx, cy
    lw = 3

    # Upper lip
    draw.line((mx - 20, my - 5, mx + 20, my - 5), fill=255, width=lw)
    # Lower lip
    draw.line((mx - 20, my + 10, mx + 20, my + 10), fill=255, width=lw)

    teeth_positions = (-12, -4, 4, 12)
    for tx in teeth_positions:
        tooth_x = mx + tx
        draw.polygon(
            [
                (tooth_x - 3, my - 5),
                (tooth_x + 3, my - 5),
                (tooth_x, my + 2),
            ],
            outline=255, fill=255,
        )
        draw.polygon(
            [
                (tooth_x - 3, my + 10),
                (tooth_x + 3, my + 10),
                (tooth_x, my + 3),
            ],
            outline=255, fill=255,
        )

    scratch_x = mx + 35
    scratch_y = my
    for i in range(3):
        off = i * 3
        draw.line(
            (scratch_x, scratch_y - 8 + off, scratch_x + 12, scratch_y + 4 + off),
            fill=255, width=1,
        )

    return img


def _load_custom_emotion_image(emotion, resources_dir, width, height):
    """Try to load a custom PNG for the given emotion from resources/emotions/."""
    if not Image:
        return None
    for ext in (".png", ".bmp", ".gif"):
        path = os.path.join(resources_dir, "emotions", emotion + ext)
        if os.path.isfile(path):
            try:
                img = Image.open(path).convert("1").resize((width, height))
                return img
            except Exception as e:
                rospy.logwarn_throttle(10.0, "display_node: failed to load %s: %s", path, e)
    return None


def _draw_emotion(emotion, width, height):
    """Draw a built-in emotion frame (128x64 1-bit). Mouth centred on display."""
    img = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(img)
    emotion = (emotion or "neutral").strip().lower()
    cx, cy = width // 2, height // 2
    scale = 2.2
    r = int(14 * scale)
    lw = max(1, int(3 * scale))

    if emotion == "happy":
        draw.arc((cx - r, cy - r, cx + r, cy + r), 0, 180, fill=255, width=lw)
    elif emotion == "sad":
        draw.arc((cx - r, cy - r, cx + r, cy + r), 180, 360, fill=255, width=lw)
    elif emotion == "angry":
        draw.line((cx - r, cy, cx + r, cy), fill=255, width=lw)
    elif emotion == "surprised":
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=255, width=lw)
    elif emotion == "excited":
        draw.arc((cx - r, cy - r, cx + r, cy + r), 0, 180, fill=255, width=lw)
    elif emotion == "tired":
        rs = max(4, r // 2)
        draw.line((cx - rs, cy, cx + rs, cy), fill=255, width=lw)
    elif emotion == "sleepy":
        # Fallback static line if animation path not used (tests / no device)
        rs = max(4, r // 2)
        draw.line((cx - rs, cy, cx + rs, cy), fill=255, width=lw)
    elif emotion == "love":
        draw.arc((cx - r, cy - r - 2, cx + r, cy + r - 2), 0, 180, fill=255, width=lw)
    elif emotion == "confused":
        n = 9
        pts = [(cx - r + (2 * r * i) // (n - 1), cy + (6 if i % 2 else -6)) for i in range(n)]
        for i in range(len(pts) - 1):
            draw.line([pts[i], pts[i + 1]], fill=255, width=lw)
    elif emotion == "scared":
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=255, width=lw)
    elif emotion == "bored":
        rs = max(6, r // 2)
        draw.line((cx - rs, cy, cx + rs, cy), fill=255, width=lw)
    elif emotion == "calm":
        draw.arc((cx - r, cy - r, cx + r, cy + r), 30, 150, fill=255, width=lw)
    elif emotion == "disgusted":
        draw.arc((cx - r, cy - r, cx + r, cy + r), 180, 360, fill=255, width=lw)
    else:  # neutral
        draw.line((cx - r, cy, cx + r, cy), fill=255, width=lw)
    return img


def _draw_oscillogram_waveform(y_values, width, height):
    """Draw an oscillogram line from W values in -1..1."""
    img = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(img)
    cy = height // 2
    n = min(len(y_values), width)
    if n < 2:
        return img
    pts = []
    for i in range(n):
        y = y_values[i]
        y_px = cy + int(y * (cy - 1))
        y_px = max(0, min(height - 1, y_px))
        pts.append((i, y_px))
    for j in range(len(pts) - 1):
        draw.line([pts[j], pts[j + 1]], fill=255, width=1)
    return img


def main():
    rospy.init_node("mouth_display_node")

    hw = sms_config.get_hardware_settings()
    W = hw.get("width", 128)
    H = hw.get("height", 64)
    I2C_PORT = hw.get("i2c_port", 1)
    I2C_ADDRESS = hw.get("i2c_address", 0x3D)
    ROTATE = int(hw.get("rotate", 0))

    emotions_cfg = sms_config.get_emotions_config()
    display_cfg = sms_config.get_display_settings()

    default_emotion = rospy.get_param("~default_emotion", display_cfg.get("default_emotion", "neutral"))
    auto_mode = rospy.get_param("~auto_mode", display_cfg.get("auto_mode", True))
    silence_return_sec = float(rospy.get_param("~silence_return_sec", display_cfg.get("silence_return_sec", 3.0)))

    resources_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "resources",
    )
    try:
        import rospkg
        resources_dir = os.path.join(rospkg.RosPack().get_path("sound_mouth_sync"), "resources")
    except Exception:
        pass

    # Preload custom emotion PNGs
    custom_emotion_cache = {}
    if Image:
        emo_dir = os.path.join(resources_dir, "emotions")
        if os.path.isdir(emo_dir):
            for fname in os.listdir(emo_dir):
                name, ext = os.path.splitext(fname)
                if ext.lower() in (".png", ".bmp", ".gif"):
                    img = _load_custom_emotion_image(name, resources_dir, W, H)
                    if img:
                        custom_emotion_cache[name.lower()] = img
                        rospy.loginfo("display_node: loaded custom emotion '%s' from %s", name, fname)

    def _norm_emotion(name):
        raw = (name or "neutral").strip().lower() or "neutral"
        if raw not in VALID_EMOTIONS and raw not in custom_emotion_cache:
            return "neutral"
        return raw

    idle_sleep_enabled = bool(rospy.get_param("~idle_sleep_enabled", display_cfg.get("idle_sleep_enabled", True)))
    idle_sleep_sec = float(rospy.get_param("~idle_sleep_sec", display_cfg.get("idle_sleep_sec", 60.0)))
    idle_sleep_emotion = _norm_emotion(rospy.get_param(
        "~idle_sleep_emotion", display_cfg.get("idle_sleep_emotion", "sleepy")))
    fall_emotion = _norm_emotion(rospy.get_param("~fall_emotion", display_cfg.get("fall_emotion", "angry")))
    posture_topic = rospy.get_param("~posture_topic", display_cfg.get("posture_topic", "/robot/posture"))
    movement_topic = rospy.get_param("~movement_topic", display_cfg.get("movement_topic", "/robot/is_moving"))
    idle_require_movement_signal = bool(rospy.get_param(
        "~idle_require_movement_signal",
        display_cfg.get("idle_require_movement_signal", True),
    ))

    user_emotion = [_norm_emotion(default_emotion)]
    # True = emotion mode, False = oscillogram mode
    emotion_mode = [True]
    last_audio_time = [0.0]
    last_activity_time = [time.time()]
    last_render_time = [0.0]
    _MIN_RENDER_INTERVAL = 0.045  # ~22 FPS max, prevents I2C bus saturation

    idle_sleep_active = [False]
    # Single source of truth for fall detection (no separate flag — avoids stale "fallen").
    posture_state = ["stand"]
    movement_signal_received = [False]
    is_moving = [False]
    sleepy_state = {}
    last_drawn_effective = [""]

    mode_pub = rospy.Publisher("/mouth/current_mode", String, queue_size=1, latch=True)
    emotion_pub = rospy.Publisher("/mouth/current_emotion", String, queue_size=1, latch=True)

    device = None
    if _LUMA_AVAILABLE:
        try:
            serial = i2c(port=I2C_PORT, address=I2C_ADDRESS)
            device = ssd1306(serial, width=W, height=H, rotate=ROTATE)
            rospy.loginfo("display_node: OLED SSD1306 %dx%d on I2C 0x%02X ready (rotate=%d)", W, H, I2C_ADDRESS, ROTATE)
        except Exception as e:
            rospy.logwarn("display_node: OLED unavailable: %s", e)
            device = None
    else:
        rospy.logwarn("display_node: luma.oled / Pillow not installed — display disabled")

    def _norm_posture_str(raw):
        return (raw or "stand").strip().lower() or "stand"

    def _robot_is_fallen():
        return _norm_posture_str(posture_state[0]) in _FALL_POSTURES

    def _compose_emotion_frame(emo):
        """Build 1-bit image for emotion (custom PNG > fall angry art > sleepy anim > builtin)."""
        if not Image:
            return None
        emo = (emo or "neutral").strip().lower()
        cached = custom_emotion_cache.get(emo)
        if cached is not None:
            return cached
        if _robot_is_fallen() and emo == fall_emotion and emo == "angry":
            return _draw_emotion_angry_fall(W, H)
        if _robot_is_fallen() and emo == fall_emotion:
            return _draw_emotion(emo, W, H)
        if emo == "sleepy":
            return _draw_sleepy_animated(W, H, sleepy_state)
        return _draw_emotion(emo, W, H)

    def _should_animate_sleepy():
        if not emotion_mode[0] or _robot_is_fallen() or device is None:
            return False
        if custom_emotion_cache.get("sleepy"):
            return False
        return _effective_emotion() == "sleepy"

    def _show_emotion(emo):
        if device is None:
            return
        try:
            img = _compose_emotion_frame(emo)
            if img is not None:
                device.display(img)
        except Exception as e:
            rospy.logdebug("display_node emotion render: %s", e)

    def _sleepy_anim_tick(_event):
        if rospy.is_shutdown() or not _should_animate_sleepy():
            return
        try:
            img = _compose_emotion_frame("sleepy")
            if img is not None:
                device.display(img)
        except Exception:
            pass

    def _show_oscillogram(values):
        if device is None:
            return
        try:
            device.display(_draw_oscillogram_waveform(values, W, H))
        except Exception as e:
            rospy.logdebug("display_node oscillogram render: %s", e)

    def _effective_emotion():
        if _robot_is_fallen():
            return fall_emotion
        if idle_sleep_active[0] and emotion_mode[0]:
            return idle_sleep_emotion
        return user_emotion[0]

    def _refresh_emotion_face():
        """Update latched emotion topic and OLED when in emotion mode."""
        if not emotion_mode[0]:
            return
        emo = _effective_emotion()
        if emo == "sleepy" and last_drawn_effective[0] != "sleepy":
            if not custom_emotion_cache.get("sleepy"):
                _sleepy_anim_reset(sleepy_state)
        last_drawn_effective[0] = emo
        emotion_pub.publish(String(data=emo))
        if device:
            _show_emotion(emo)

    def _bump_activity_timer():
        """Reset idle-sleep countdown; exit idle-sleep overlay if active."""
        last_activity_time[0] = time.time()
        if idle_sleep_active[0]:
            idle_sleep_active[0] = False
            _refresh_emotion_face()

    def _on_audible_detected(now, wave_values):
        """
        Shared path: sound from /mouth/audio_wave and/or /audio/level.
        wave_values: list of floats or None (level-only: draw flat line until next wave).
        """
        was_idle_sleep_overlay = idle_sleep_active[0]
        last_audio_time[0] = now
        last_activity_time[0] = now
        if idle_sleep_active[0]:
            idle_sleep_active[0] = False
        if auto_mode and emotion_mode[0]:
            emotion_mode[0] = False
            mode_pub.publish(String(data="oscillogram"))
            if wave_values is not None:
                _show_oscillogram(wave_values)
            else:
                _show_oscillogram([0.0] * W)
            last_render_time[0] = now
        elif was_idle_sleep_overlay and emotion_mode[0]:
            _refresh_emotion_face()

    def on_posture(msg):
        prev = _norm_posture_str(posture_state[0])
        new = _norm_posture_str(msg.data)
        posture_state[0] = new
        prev_fallen = prev in _FALL_POSTURES
        new_fallen = new in _FALL_POSTURES
        if new_fallen and not prev_fallen:
            idle_sleep_active[0] = False
            if not emotion_mode[0]:
                emotion_mode[0] = True
                mode_pub.publish(String(data="emotion"))
            _refresh_emotion_face()
            rospy.logwarn("display_node: robot fallen (%s) — showing '%s'", new, fall_emotion)
        elif not new_fallen and prev_fallen:
            idle_sleep_active[0] = False
            _bump_activity_timer()
            rospy.loginfo("display_node: robot upright — emotion '%s'", user_emotion[0])
            _refresh_emotion_face()

    def on_moving(msg):
        movement_signal_received[0] = True
        is_moving[0] = bool(msg.data)
        if msg.data:
            _bump_activity_timer()

    def on_idle_tick(_event):
        if rospy.is_shutdown() or _robot_is_fallen():
            return
        if not idle_sleep_enabled:
            if idle_sleep_active[0]:
                idle_sleep_active[0] = False
                _refresh_emotion_face()
            return
        if not emotion_mode[0]:
            return
        if idle_require_movement_signal and not movement_signal_received[0]:
            return
        elapsed_idle = time.time() - last_activity_time[0]
        if elapsed_idle >= idle_sleep_sec:
            if not idle_sleep_active[0]:
                idle_sleep_active[0] = True
                _refresh_emotion_face()
        elif idle_sleep_active[0]:
            idle_sleep_active[0] = False
            _refresh_emotion_face()

    # Initial state
    mode_pub.publish(String(data="emotion"))
    emotion_pub.publish(String(data=user_emotion[0]))
    if device:
        _show_emotion(user_emotion[0])

    def on_mode(msg):
        raw = (msg.data or "").strip().lower()
        if raw == "oscillogram":
            if _robot_is_fallen():
                rospy.logdebug_throttle(5.0, "display_node: oscillogram ignored while robot fallen")
                return
            emotion_mode[0] = False
            mode_pub.publish(String(data="oscillogram"))
            if device:
                _show_oscillogram([0.0] * W)
            rospy.loginfo("display_node: mode -> oscillogram")
        elif raw == "emotion":
            emotion_mode[0] = True
            mode_pub.publish(String(data="emotion"))
            _refresh_emotion_face()
            rospy.loginfo("display_node: mode -> emotion")

    def on_emotion(msg):
        raw = _norm_emotion((msg.data or "neutral").strip().lower() or "neutral")
        user_emotion[0] = raw
        _bump_activity_timer()
        if emotion_mode[0]:
            _refresh_emotion_face()

    def on_audio_wave(msg):
        if _robot_is_fallen():
            return
        if not msg.data:
            return
        values = list(msg.data)

        now = time.time()

        if not emotion_mode[0]:
            last_activity_time[0] = now

        audible = _mouth_audio_gates.waveform_is_audible(values)

        if audible:
            _on_audible_detected(now, values)

        if not emotion_mode[0]:
            if (now - last_render_time[0]) >= _MIN_RENDER_INTERVAL:
                _show_oscillogram(values)
                last_render_time[0] = now

    audio_level_topic = rospy.get_param(
        "~audio_level_topic",
        display_cfg.get("audio_level_topic", "/audio/level"),
    )

    def on_audio_level(msg):
        """Same energy scale as audio_capture_node (RMS / full-scale); catches quiet files."""
        if _robot_is_fallen():
            return
        try:
            level = abs(float(msg.data))
        except (TypeError, ValueError):
            return
        if level < _mouth_audio_gates.AUDIO_LEVEL_THRESHOLD:
            return
        now = time.time()
        if not emotion_mode[0]:
            last_activity_time[0] = now
        _on_audible_detected(now, None)

    rospy.Subscriber("/mouth/mode", String, on_mode, queue_size=1)
    rospy.Subscriber("/mouth/emotion", String, on_emotion, queue_size=1)
    rospy.Subscriber("/mouth/audio_wave", Float32MultiArray, on_audio_wave, queue_size=1)
    rospy.Subscriber(audio_level_topic, Float32, on_audio_level, queue_size=1)
    rospy.loginfo("display_node: also subscribing %s for sound (oscillogram wake)", audio_level_topic)
    rospy.Subscriber(posture_topic, String, on_posture, queue_size=1)
    rospy.Subscriber(movement_topic, Bool, on_moving, queue_size=1)

    # Timer for auto-return to emotion mode after silence
    def _auto_return_tick(_event):
        if rospy.is_shutdown() or _robot_is_fallen():
            return
        if not auto_mode or emotion_mode[0]:
            return
        elapsed = time.time() - last_audio_time[0]
        if elapsed >= silence_return_sec:
            emotion_mode[0] = True
            mode_pub.publish(String(data="emotion"))
            _refresh_emotion_face()

    if auto_mode:
        rospy.Timer(rospy.Duration(0.3), _auto_return_tick)

    rospy.Timer(rospy.Duration(0.5), on_idle_tick)
    rospy.Timer(rospy.Duration(0.12), _sleepy_anim_tick)

    if device:
        def shutdown_display():
            try:
                device.display(_draw_emotion("neutral", W, H))
                time.sleep(0.15)
            except Exception:
                pass
        rospy.on_shutdown(shutdown_display)
        atexit.register(shutdown_display)

    rospy.loginfo(
        "display_node started: mode=emotion, user_emotion=%s, auto_mode=%s, "
        "idle_sleep=%s (%.1fs), fall_emotion=%s",
        user_emotion[0], auto_mode, idle_sleep_enabled, idle_sleep_sec, fall_emotion,
    )
    rospy.spin()


if __name__ == "__main__":
    try:
        main()
    except rospy.ROSInterruptException:
        pass
