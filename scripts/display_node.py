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

Publications:
  /mouth/current_mode    (String, latch)
  /mouth/current_emotion (String, latch)

Parameters:
  ~default_emotion         initial emotion (default: neutral)
  ~auto_mode               auto-switch to oscillogram on sound, back on silence (default: true)
  ~silence_return_sec      seconds of silence before returning to emotion mode (default: 3.0)
  ~i2c_port                I2C port number (default: 1)
  ~i2c_address             I2C address (default: 0x3D = 61)
  ~width                   display width  (default: 128)
  ~height                  display height (default: 64)
"""
from __future__ import annotations

import atexit
import math
import os
import sys
import time

import rospy
from std_msgs.msg import String, Float32MultiArray

try:
    import rospkg
    _pkg_path = rospkg.RosPack().get_path("sound_mouth_sync")
    sys.path.insert(0, os.path.join(_pkg_path, "scripts"))
except Exception:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sms_config

try:
    from luma.core.interface.serial import i2c
    from luma.oled.device import ssd1306
    from PIL import Image, ImageDraw
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

_SILENCE_THRESHOLD = 0.02


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
    elif emotion in ("sleepy", "tired"):
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

    current_emotion = [default_emotion if default_emotion in VALID_EMOTIONS or default_emotion in custom_emotion_cache else "neutral"]
    # True = emotion mode, False = oscillogram mode
    emotion_mode = [True]
    last_audio_time = [0.0]

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

    def _show_emotion(emo):
        if device is None:
            return
        try:
            img = custom_emotion_cache.get(emo)
            if img is None:
                img = _draw_emotion(emo, W, H)
            device.display(img)
        except Exception as e:
            rospy.logdebug("display_node emotion render: %s", e)

    def _show_oscillogram(values):
        if device is None:
            return
        try:
            device.display(_draw_oscillogram_waveform(values, W, H))
        except Exception as e:
            rospy.logdebug("display_node oscillogram render: %s", e)

    # Initial state
    mode_pub.publish(String(data="emotion"))
    emotion_pub.publish(String(data=current_emotion[0]))
    if device:
        _show_emotion(current_emotion[0])

    def on_mode(msg):
        raw = (msg.data or "").strip().lower()
        if raw == "oscillogram":
            emotion_mode[0] = False
            mode_pub.publish(String(data="oscillogram"))
            if device:
                _show_oscillogram([0.0] * W)
            rospy.loginfo("display_node: mode -> oscillogram")
        elif raw == "emotion":
            emotion_mode[0] = True
            mode_pub.publish(String(data="emotion"))
            _show_emotion(current_emotion[0])
            rospy.loginfo("display_node: mode -> emotion")

    def on_emotion(msg):
        raw = (msg.data or "neutral").strip().lower() or "neutral"
        if raw not in VALID_EMOTIONS and raw not in custom_emotion_cache:
            raw = "neutral"
        current_emotion[0] = raw
        emotion_pub.publish(String(data=raw))
        if emotion_mode[0]:
            _show_emotion(raw)

    def on_audio_wave(msg):
        if not msg.data:
            return
        values = list(msg.data)

        rms = 0.0
        n = len(values)
        if n > 0:
            total = sum(v * v for v in values)
            rms = (total / n) ** 0.5

        if rms >= _SILENCE_THRESHOLD:
            last_audio_time[0] = time.time()
            if auto_mode and emotion_mode[0]:
                emotion_mode[0] = False
                mode_pub.publish(String(data="oscillogram"))

        if not emotion_mode[0]:
            _show_oscillogram(values)

    rospy.Subscriber("/mouth/mode", String, on_mode, queue_size=1)
    rospy.Subscriber("/mouth/emotion", String, on_emotion, queue_size=1)
    rospy.Subscriber("/mouth/audio_wave", Float32MultiArray, on_audio_wave, queue_size=5)

    # Timer for auto-return to emotion mode after silence
    def _auto_return_tick(_event):
        if rospy.is_shutdown():
            return
        if not auto_mode or emotion_mode[0]:
            return
        elapsed = time.time() - last_audio_time[0]
        if elapsed >= silence_return_sec:
            emotion_mode[0] = True
            mode_pub.publish(String(data="emotion"))
            _show_emotion(current_emotion[0])

    if auto_mode:
        rospy.Timer(rospy.Duration(1.0), _auto_return_tick)

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
        "display_node started: mode=%s, default_emotion=%s, auto_mode=%s",
        "emotion", current_emotion[0], auto_mode,
    )
    rospy.spin()


if __name__ == "__main__":
    try:
        main()
    except rospy.ROSInterruptException:
        pass
