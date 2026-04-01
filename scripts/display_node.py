#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
display_node — manages the OLED SSD1306 128x64 (I2C 0x3D) mounted on the robot's
head (mouth area). Two display modes:

  emotion      — mouth expression frames from mouth_emotion_render (PIL 1-bit), composed here for I2C
  oscillogram  — real-time audio waveform received from audio_capture_node (drawn only in this file)

Subscriptions:
  /mouth/effective_mode    (String)            "emotion" | "oscillogram"
  /mouth/effective_emotion (String)            emotion name
  /mouth/audio_wave  (Float32MultiArray) 128 values in -1..1
  /audio/level       (Float32)           chunk RMS 0..1 (audio_capture) — extra trigger for oscillogram
  /robot/posture     (String)            stand | fall_* (from joystick_control)
  /robot/is_moving   (Bool)              gait moving (from joystick_control)
  /oled_3d/active_driver (String)        motik = отдать I2C motik_node; sound_mouth_sync = снова рот

Publications:
  /mouth/current_mode    (String, latch)
  /mouth/current_emotion (String, latch)
  /oled_3d/active_driver (String, latch) — sound_mouth_sync | motik; переключение владельца OLED с rosrun motik

Parameters:
  ~default_emotion         initial emotion (default: neutral)
  ~start_display_mode      oscillogram | emotion — режим после старта (YAML: display.start_display_mode)
  ~auto_mode               auto-switch to oscillogram on sound, back on silence (default: true)
  ~silence_return_sec      seconds of silence before returning to emotion mode (default: 3.0)
  ~idle_sleep_enabled     enable sleepy face after idle (default: true)
  ~idle_sleep_sec         idle timeout seconds (default: 60)
  ~idle_sleep_emotion     legacy single idle emotion if ~idle_sleep_emotions unset
  ~idle_sleep_emotions    list of emotions; random choice when idle triggers (default: sleepy, cat, sleep)
  ~idle_sleep_rotate_sec  while idle overlay active, switch emotion every N s (0 = no rotation)
  ~fall_emotion           emotion when fallen (default: angry)
  ~posture_topic           remapped /robot/posture
  ~movement_topic          remapped /robot/is_moving
  ~idle_require_movement_signal  require /robot/is_moving before idle sleep (default: true)
  ~audio_level_topic             subscribe for /audio/level (default from YAML)
  ~mode_topic                    mode source topic (default: /mouth/effective_mode)
  ~emotion_topic                 emotion source topic (default: /mouth/effective_emotion)
  ~i2c_port                I2C port number (default: 1)
  ~i2c_address             I2C address (default: 0x3D = 61)
  ~mouth_oled_startup_delay_sec  wait before opening mouth OLED (let 0x3C info display init first)
  ~width                   display width  (default: 128)
  ~height                  display height (default: 64)
"""
from __future__ import annotations

import atexit
import os
import random
import sys
import threading
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

import mouth_audio_gates as _mouth_audio_gates
import mouth_emotion_render as mer
import sms_config

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

# Emotion pixel art lives in mouth_emotion_render (no rospy). Oscillogram stays here.

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


def _wait_for_robot_standup(log_prefix="mouth_display_node"):
    """
    Ждём init_pose/init_finish=True (ainex_controller) до открытия I2C OLED.

    ~wait_standup_timeout_sec: 0 = без лимита; >0 — секунды.
    ~proceed_without_standup: при таймауте >0 — true = продолжить без подъёма.
    """
    timeout = float(rospy.get_param("~wait_standup_timeout_sec", 0.0))
    _proceed = rospy.get_param("~proceed_without_standup", False)
    proceed = (
        _proceed is True
        or (isinstance(_proceed, str) and _proceed.strip().lower() in ("true", "1", "yes"))
    )
    start = time.time()
    while not rospy.is_shutdown():
        if rospy.get_param("init_pose/init_finish", False):
            rospy.loginfo("%s: робот встал (init_pose/init_finish), открываем OLED", log_prefix)
            return True
        if timeout > 0.0 and (time.time() - start) > timeout:
            if proceed:
                rospy.logwarn(
                    "%s: таймаут ожидания подъёма (%.0f с), proceed_without_standup=true",
                    log_prefix,
                    timeout,
                )
                return False
            rospy.logerr(
                "%s: таймаут init_pose/init_finish (%.0f с). Стенд: wait_standup_timeout_sec>0 "
                "и proceed_without_standup:=true",
                log_prefix,
                timeout,
            )
            rospy.signal_shutdown("standup timeout (init_pose/init_finish)")
            raise rospy.ROSInterruptException()
        time.sleep(0.5)
    return False


# Переключение владельца OLED 0x3D с пакетом motik (без одновременного I2C).
DRIVER_TOPIC = "/oled_3d/active_driver"
DRIVER_MOUTH = "sound_mouth_sync"
DRIVER_MOTIK = "motik"


def _normalize_oled_driver(raw):
    s = (raw or "").strip().lower()
    if s in ("sound_mouth_sync", "mouth", "sound_sync", ""):
        return DRIVER_MOUTH
    if s == "motik":
        return DRIVER_MOTIK
    return DRIVER_MOUTH


def main():
    rospy.init_node("mouth_display_node")

    hw = sms_config.get_hardware_settings()
    W = hw.get("width", 128)
    H = hw.get("height", 64)
    I2C_PORT = hw.get("i2c_port", 1)
    I2C_ADDRESS = hw.get("i2c_address", 0x3D)
    ROTATE = int(hw.get("rotate", 0))
    if rospy.has_param("~i2c_address"):
        v = rospy.get_param("~i2c_address")
        I2C_ADDRESS = int(str(v).strip(), 0) if not isinstance(v, int) else int(v)
    if I2C_ADDRESS == 0x3C:
        rospy.logwarn(
            "display_node: i2c_address 0x3C is for the system info OLED (oled_display.py). "
            "Mouth must use 0x3D — fix sound_mouth_sync.yaml hardware.i2c_address",
        )

    emotions_cfg = sms_config.get_emotions_config()
    display_cfg = sms_config.get_display_settings()

    default_emotion = rospy.get_param(
        "~default_emotion", display_cfg.get("default_emotion", "neutral"))
    auto_mode = rospy.get_param(
        "~auto_mode", display_cfg.get("auto_mode", True))
    silence_return_sec = float(rospy.get_param(
        "~silence_return_sec", display_cfg.get("silence_return_sec", 3.0)))

    resources_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "resources",
    )
    try:
        import rospkg
        resources_dir = os.path.join(
            rospkg.RosPack().get_path("sound_mouth_sync"), "resources")
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
                    img = mer.load_custom_emotion_image(name, resources_dir, W, H)
                    if img:
                        custom_emotion_cache[name.lower()] = img
                        rospy.loginfo(
                            "display_node: loaded custom emotion '%s' from %s", name, fname)

    cat_animation_frames = []
    for key in ("cat_frame0", "cat_frame1"):
        cimg = mer.load_custom_emotion_image(key, resources_dir, W, H)
        if cimg is not None:
            cat_animation_frames.append(cimg)
    if len(cat_animation_frames) >= 2:
        rospy.loginfo(
            "display_node: cat animation: %d frames (cat_frame0.png / cat_frame1.png)",
            len(cat_animation_frames),
        )
    for ghost in ("cat_frame0", "cat_frame1"):
        custom_emotion_cache.pop(ghost, None)
    if len(cat_animation_frames) >= 2:
        custom_emotion_cache.pop("cat", None)

    sleep_animation_frames = []
    for key in ("sleep_frame1", "sleep_frame2", "sleep_frame3"):
        simg = mer.load_custom_emotion_image(key, resources_dir, W, H)
        if simg is not None:
            sleep_animation_frames.append(simg)
    if len(sleep_animation_frames) >= 2:
        rospy.loginfo(
            "display_node: sleep animation: %d frames (sleep_frame1.png … sleep_frame3.png)",
            len(sleep_animation_frames),
        )
    for ghost in ("sleep_frame1", "sleep_frame2", "sleep_frame3"):
        custom_emotion_cache.pop(ghost, None)
    if len(sleep_animation_frames) >= 2:
        custom_emotion_cache.pop("sleep", None)

    idle_faces = mer.load_idle_faces(resources_dir, W, H)
    if idle_faces:
        rospy.loginfo("display_node: loaded %d idle face animation(s): %s",
                      len(idle_faces), ", ".join(f["name"] for f in idle_faces))
    else:
        rospy.loginfo("display_node: no idle face animations in resources/idle_faces/ — "
                      "using 3 built-in idle anims: %s", ", ".join(mer._BUILTIN_IDLE_ANIM_NAMES))

    idle_face_frame_ms = int(display_cfg.get("idle_face_frame_ms", 120))

    def _norm_emotion(name):
        raw = (name or "neutral").strip().lower() or "neutral"
        if raw not in mer.VALID_EMOTIONS and raw not in custom_emotion_cache:
            return "neutral"
        return raw

    idle_sleep_enabled = bool(rospy.get_param(
        "~idle_sleep_enabled", display_cfg.get("idle_sleep_enabled", True)))
    idle_sleep_sec = float(rospy.get_param(
        "~idle_sleep_sec", display_cfg.get("idle_sleep_sec", 60.0)))
    idle_sleep_rotate_sec = float(rospy.get_param(
        "~idle_sleep_rotate_sec",
        display_cfg.get("idle_sleep_rotate_sec", 30.0),
    ))
    _idle_pool_raw = rospy.get_param(
        "~idle_sleep_emotions", display_cfg.get("idle_sleep_emotions"))
    if _idle_pool_raw is None:
        _idle_pool_raw = rospy.get_param(
            "~idle_sleep_emotion", display_cfg.get("idle_sleep_emotion", "sleepy"))
    if isinstance(_idle_pool_raw, str):
        _idle_pool_raw = [_idle_pool_raw]
    elif not isinstance(_idle_pool_raw, (list, tuple)):
        _idle_pool_raw = ["sleepy"]
    idle_sleep_emotion_pool = [
        _norm_emotion(str(x).strip()) for x in _idle_pool_raw if str(x).strip()
    ]
    if not idle_sleep_emotion_pool:
        idle_sleep_emotion_pool = ["sleepy"]
    fall_emotion = _norm_emotion(rospy.get_param(
        "~fall_emotion", display_cfg.get("fall_emotion", "angry")))
    posture_topic = rospy.get_param(
        "~posture_topic", display_cfg.get("posture_topic", "/robot/posture"))
    movement_topic = rospy.get_param(
        "~movement_topic", display_cfg.get("movement_topic", "/robot/is_moving"))
    idle_require_movement_signal = bool(rospy.get_param(
        "~idle_require_movement_signal",
        display_cfg.get("idle_require_movement_signal", True),
    ))

    user_emotion = [_norm_emotion(default_emotion)]
    # True = emotion mode, False = oscillogram mode
    _start_mode_raw = (
        rospy.get_param("~start_display_mode", display_cfg.get("start_display_mode", "oscillogram"))
        or "oscillogram"
    ).strip().lower()
    emotion_mode = [_start_mode_raw != "oscillogram"]
    last_audio_time = [0.0]
    last_activity_time = [time.time()]
    last_render_time = [0.0]
    _MIN_RENDER_INTERVAL = 0.045  # ~22 FPS max, prevents I2C bus saturation

    idle_sleep_active = [False]
    idle_sleep_pick = [idle_sleep_emotion_pool[0]]
    idle_sleep_last_switch = [0.0]
    idle_sleep_last_shown_pick = [None]
    # Single source of truth for fall detection (no separate flag — avoids stale "fallen").
    posture_state = ["stand"]
    movement_signal_received = [False]
    is_moving = [False]
    sleepy_state = {}
    idle_face_state = {
        "anim_idx": -1,
        "frame_idx": 0,
        "last_frame_time": 0.0,
    }
    builtin_idle_state = {
        "anim_idx": -1,
        "start": 0.0,
    }
    last_drawn_effective = [""]

    mode_pub = rospy.Publisher(
        "/mouth/current_mode", String, queue_size=1, latch=True)
    emotion_pub = rospy.Publisher(
        "/mouth/current_emotion", String, queue_size=1, latch=True)

    mouth_delay = float(
        rospy.get_param(
            "~mouth_oled_startup_delay_sec",
            hw.get("mouth_oled_startup_delay_sec", 7.0),
        )
    )
    if mouth_delay > 0 and _LUMA_AVAILABLE:
        rospy.loginfo(
            "display_node: waiting %.1fs before opening mouth OLED at I2C 0x%02X (info OLED 0x3C first)",
            mouth_delay,
            I2C_ADDRESS,
        )
        deadline = time.time() + mouth_delay
        while time.time() < deadline and not rospy.is_shutdown():
            rospy.sleep(min(0.2, max(0.0, deadline - time.time())))

    if not _wait_for_robot_standup("mouth_display_node"):
        if rospy.is_shutdown():
            raise rospy.ROSInterruptException()

    active_driver = [_normalize_oled_driver(rospy.get_param("/oled_3d/active_driver_default", DRIVER_MOUTH))]
    pub_driver = rospy.Publisher(DRIVER_TOPIC, String, queue_size=1, latch=True)

    # Protect OLED writes и переключение с motik.
    display_lock = threading.Lock()
    device = None

    def _mouth_owns_oled():
        return active_driver[0] == DRIVER_MOUTH

    def _release_oled():
        nonlocal device
        with display_lock:
            if device is None:
                return
            try:
                if hasattr(device, "hide"):
                    device.hide()
            except Exception:
                pass
            try:
                device.clear()
            except Exception:
                pass
            device = None

    def _acquire_oled():
        nonlocal device
        if not _LUMA_AVAILABLE or not _mouth_owns_oled():
            return
        with display_lock:
            if device is not None:
                return
            try:
                ser = i2c(port=I2C_PORT, address=I2C_ADDRESS)
                device = ssd1306(ser, width=W, height=H, rotate=ROTATE)
                rospy.loginfo(
                    "display_node: OLED SSD1306 %dx%d on I2C 0x%02X ready (rotate=%d)",
                    W, H, I2C_ADDRESS, ROTATE,
                )
            except Exception as e:
                rospy.logwarn("display_node: OLED unavailable: %s", e)
                device = None

    if _LUMA_AVAILABLE:
        if _mouth_owns_oled():
            _acquire_oled()
            if device is None:
                rospy.logwarn(
                    "display_node: OLED не открылся при старте (I2C 0x%02X, порт %s). "
                    "Проверьте шину, права (группа i2c), что /oled_3d/active_driver = sound_mouth_sync, "
                    "и что motik не держит дисплей. Повторная попытка каждые 5 с.",
                    I2C_ADDRESS,
                    I2C_PORT,
                )
    else:
        rospy.logwarn(
            "display_node: luma.oled / Pillow not installed — display disabled")

    def _norm_posture_str(raw):
        return (raw or "stand").strip().lower() or "stand"

    def _robot_is_fallen():
        return _norm_posture_str(posture_state[0]) in mer.FALL_POSTURES

    def _idle_face_pick_random():
        """Select a random idle face animation (different from current if possible)."""
        mer.idle_face_pick_random(
            idle_faces, idle_face_state, builtin_idle_state, sleepy_state)

    def _compose_emotion_frame(emo):
        """Build 1-bit image for emotion (custom PNG > fall angry art > idle face > sleepy anim > builtin)."""
        if not Image:
            return None
        return mer.compose_emotion_frame(
            emo,
            W,
            H,
            custom_emotion_cache=custom_emotion_cache,
            cat_animation_frames=cat_animation_frames,
            sleep_animation_frames=sleep_animation_frames,
            idle_faces=idle_faces,
            idle_sleep_active=idle_sleep_active[0],
            robot_fallen=_robot_is_fallen(),
            fall_emotion=fall_emotion,
            idle_face_state=idle_face_state,
            builtin_idle_state=builtin_idle_state,
            sleepy_state=sleepy_state,
        )

    def _should_animate_idle():
        """True if we should keep ticking the idle animation."""
        if not _mouth_owns_oled() or not emotion_mode[0] or _robot_is_fallen() or device is None:
            return False
        if not idle_sleep_active[0]:
            return False
        return True

    def _should_animate_cat():
        if (
            not emotion_mode[0]
            or _robot_is_fallen()
            or device is None
            or not _mouth_owns_oled()
        ):
            return False
        if idle_sleep_active[0]:
            return False
        return (
            _effective_emotion() == "cat"
            and len(cat_animation_frames) >= 2
        )

    def _should_animate_sleep():
        if (
            not emotion_mode[0]
            or _robot_is_fallen()
            or device is None
            or not _mouth_owns_oled()
        ):
            return False
        if idle_sleep_active[0]:
            return False
        return (
            _effective_emotion() == "sleep"
            and len(sleep_animation_frames) >= 2
        )

    def _should_animate_sleepy():
        if (
            not emotion_mode[0]
            or _robot_is_fallen()
            or device is None
            or not _mouth_owns_oled()
        ):
            return False
        if idle_sleep_active[0]:
            return False
        if custom_emotion_cache.get("sleepy"):
            return False
        return _effective_emotion() == "sleepy"

    def _show_emotion(emo):
        if not _mouth_owns_oled() or device is None:
            return
        try:
            with display_lock:
                # Re-check under lock: during mode switches, an already-started
                # emotion render can otherwise overwrite oscillogram.
                if emotion_mode[0] and device is not None:
                    img = _compose_emotion_frame(emo)
                    if img is not None:
                        device.display(img)
        except Exception as e:
            rospy.logdebug("display_node emotion render: %s", e)

    def _idle_anim_tick(_event):
        try:
            if rospy.is_shutdown() or not _should_animate_idle():
                return
            with display_lock:
                if rospy.is_shutdown() or not _should_animate_idle():
                    return
                emo = _effective_emotion()
                img = _compose_emotion_frame(emo)
                if img is not None:
                    device.display(img)
        except Exception:
            pass

    def _cat_anim_tick(_event):
        try:
            if rospy.is_shutdown() or not _should_animate_cat():
                return
            with display_lock:
                if rospy.is_shutdown() or not _should_animate_cat():
                    return
                img = _compose_emotion_frame("cat")
                if img is not None:
                    device.display(img)
        except Exception:
            pass

    def _sleep_anim_tick(_event):
        try:
            if rospy.is_shutdown() or not _should_animate_sleep():
                return
            with display_lock:
                if rospy.is_shutdown() or not _should_animate_sleep():
                    return
                img = _compose_emotion_frame("sleep")
                if img is not None:
                    device.display(img)
        except Exception:
            pass

    def _sleepy_anim_tick(_event):
        try:
            if rospy.is_shutdown() or not _should_animate_sleepy():
                return
            with display_lock:
                if rospy.is_shutdown() or not _should_animate_sleepy():
                    return
                img = _compose_emotion_frame("sleepy")
                if img is not None:
                    device.display(img)
        except Exception:
            pass

    def _show_oscillogram(values):
        if not _mouth_owns_oled() or device is None:
            return
        try:
            with display_lock:
                # If we already switched back to emotion mode, don't overwrite.
                if not emotion_mode[0] and device is not None:
                    device.display(_draw_oscillogram_waveform(values, W, H))
        except Exception as e:
            rospy.logdebug("display_node oscillogram render: %s", e)

    def _effective_emotion():
        if _robot_is_fallen():
            return fall_emotion
        if idle_sleep_active[0] and emotion_mode[0]:
            return idle_sleep_pick[0]
        return user_emotion[0]

    def _refresh_emotion_face():
        """Update latched emotion topic and OLED when in emotion mode."""
        if not emotion_mode[0]:
            return
        if not _mouth_owns_oled():
            return
        emo = _effective_emotion()
        if idle_sleep_active[0] and last_drawn_effective[0] != "__idle_face__":
            if idle_faces:
                _idle_face_pick_random()
        if emo == "sleepy" and not custom_emotion_cache.get("sleepy"):
            if idle_sleep_active[0]:
                if idle_sleep_last_shown_pick[0] != "sleepy":
                    mer.sleepy_anim_reset(sleepy_state)
            elif last_drawn_effective[0] != "sleepy":
                mer.sleepy_anim_reset(sleepy_state)
        if idle_sleep_active[0]:
            idle_sleep_last_shown_pick[0] = emo
        else:
            idle_sleep_last_shown_pick[0] = None
        last_drawn_effective[0] = "__idle_face__" if idle_sleep_active[0] else emo
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
        prev_fallen = prev in mer.FALL_POSTURES
        new_fallen = new in mer.FALL_POSTURES
        if new_fallen and not prev_fallen:
            idle_sleep_active[0] = False
            if not emotion_mode[0]:
                emotion_mode[0] = True
                mode_pub.publish(String(data="emotion"))
            _refresh_emotion_face()
            rospy.logwarn(
                "display_node: robot fallen (%s) — showing '%s'", new, fall_emotion)
        elif not new_fallen and prev_fallen:
            idle_sleep_active[0] = False
            _bump_activity_timer()
            rospy.loginfo(
                "display_node: robot upright — emotion '%s'", user_emotion[0])
            _refresh_emotion_face()

    def on_moving(msg):
        movement_signal_received[0] = True
        is_moving[0] = bool(msg.data)
        if msg.data:
            _bump_activity_timer()

    _node_start_time = time.time()
    _MOVEMENT_SIGNAL_GRACE_SEC = 30.0

    def on_idle_tick(_event):
        if rospy.is_shutdown() or _robot_is_fallen():
            return
        if not _mouth_owns_oled():
            return
        if not idle_sleep_enabled:
            if idle_sleep_active[0]:
                idle_sleep_active[0] = False
                _refresh_emotion_face()
            return
        if not emotion_mode[0]:
            return
        if idle_require_movement_signal and not movement_signal_received[0]:
            if (time.time() - _node_start_time) < _MOVEMENT_SIGNAL_GRACE_SEC:
                return
        elapsed_idle = time.time() - last_activity_time[0]
        if elapsed_idle >= idle_sleep_sec:
            if not idle_sleep_active[0]:
                idle_sleep_active[0] = True
                idle_sleep_pick[0] = random.choice(idle_sleep_emotion_pool)
                idle_sleep_last_switch[0] = time.time()
                rospy.loginfo(
                    "display_node: idle overlay emotion=%s (pool=%s)",
                    idle_sleep_pick[0],
                    idle_sleep_emotion_pool,
                )
                _refresh_emotion_face()
            elif (
                idle_sleep_rotate_sec > 0
                and len(idle_sleep_emotion_pool) > 1
                and (time.time() - idle_sleep_last_switch[0]) >= idle_sleep_rotate_sec
            ):
                prev = idle_sleep_pick[0]
                alts = [e for e in idle_sleep_emotion_pool if e != prev]
                idle_sleep_pick[0] = random.choice(alts) if alts else prev
                idle_sleep_last_switch[0] = time.time()
                rospy.loginfo(
                    "display_node: idle overlay rotate -> %s (was %s)",
                    idle_sleep_pick[0],
                    prev,
                )
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
                rospy.logdebug_throttle(
                    5.0, "display_node: oscillogram ignored while robot fallen")
                return
            _bump_activity_timer()
            emotion_mode[0] = False
            mode_pub.publish(String(data="oscillogram"))
            if device and _mouth_owns_oled():
                _show_oscillogram([0.0] * W)
            rospy.loginfo("display_node: mode -> oscillogram")
        elif raw == "emotion":
            _bump_activity_timer()
            emotion_mode[0] = True
            mode_pub.publish(String(data="emotion"))
            _refresh_emotion_face()
            rospy.loginfo("display_node: mode -> emotion")

    def on_emotion(msg):
        raw = _norm_emotion(
            (msg.data or "neutral").strip().lower() or "neutral")
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
    mode_topic = rospy.get_param("~mode_topic", "/mouth/effective_mode")
    emotion_topic = rospy.get_param("~emotion_topic", "/mouth/effective_emotion")

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

    def _on_oled_driver(msg):
        n = _normalize_oled_driver(msg.data)
        if n == active_driver[0]:
            return
        active_driver[0] = n
        rospy.loginfo("display_node: %s -> %s", DRIVER_TOPIC, n)
        if n == DRIVER_MOTIK:
            _release_oled()
        else:
            rospy.sleep(0.25)
            _acquire_oled()
            if emotion_mode[0]:
                _refresh_emotion_face()
            elif device is not None:
                _show_oscillogram([0.0] * W)

    pub_driver.publish(String(data=active_driver[0]))
    rospy.Subscriber(DRIVER_TOPIC, String, _on_oled_driver, queue_size=5)

    if not emotion_mode[0]:
        mode_pub.publish(String(data="oscillogram"))
        if device is not None and _mouth_owns_oled():
            _show_oscillogram([0.0] * W)

    rospy.Subscriber(mode_topic, String, on_mode, queue_size=1)
    rospy.Subscriber(emotion_topic, String, on_emotion, queue_size=1)
    rospy.Subscriber("/mouth/audio_wave", Float32MultiArray,
                     on_audio_wave, queue_size=1)
    rospy.Subscriber(audio_level_topic, Float32, on_audio_level, queue_size=1)
    rospy.loginfo(
        "display_node: also subscribing %s for sound (oscillogram wake)", audio_level_topic)
    rospy.loginfo(
        "display_node: control topics mode=%s emotion=%s", mode_topic, emotion_topic)
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
    rospy.Timer(rospy.Duration(0.12), _idle_anim_tick)
    rospy.Timer(rospy.Duration(0.12), _sleepy_anim_tick)
    rospy.Timer(rospy.Duration(0.12), _cat_anim_tick)
    rospy.Timer(rospy.Duration(0.12), _sleep_anim_tick)

    def _oled_retry_tick(_event):
        """Повторно открыть SSD1306, если при старте I2C был занят или ошибка была временной."""
        if rospy.is_shutdown() or not _LUMA_AVAILABLE or not _mouth_owns_oled():
            return
        with display_lock:
            if device is not None:
                return
        _acquire_oled()
        if device is not None:
            rospy.loginfo("display_node: OLED подключён после повторной попытки")
            if emotion_mode[0]:
                _refresh_emotion_face()
            else:
                _show_oscillogram([0.0] * W)

    rospy.Timer(rospy.Duration(5.0), _oled_retry_tick)

    _mouth_redraw_after = float(
        rospy.get_param(
            "~mouth_display_redraw_after_sec",
            display_cfg.get("mouth_display_redraw_after_sec", 0.0),
        )
    )
    if _mouth_redraw_after > 0 and _LUMA_AVAILABLE:

        def _delayed_mouth_redraw(_evt):
            if rospy.is_shutdown() or not _mouth_owns_oled():
                return
            with display_lock:
                if device is None:
                    return
            rospy.loginfo(
                "display_node: mouth_display_redraw_after_sec=%.1fs — redrawing mouth OLED",
                _mouth_redraw_after,
            )
            if emotion_mode[0]:
                _refresh_emotion_face()
            else:
                _show_oscillogram([0.0] * W)

        rospy.Timer(
            rospy.Duration(_mouth_redraw_after), _delayed_mouth_redraw, oneshot=True
        )

    def shutdown_display():
        if not _mouth_owns_oled() or device is None:
            return
        try:
            device.display(mer.draw_emotion("neutral", W, H))
            time.sleep(0.15)
        except Exception:
            pass

    rospy.on_shutdown(shutdown_display)
    atexit.register(shutdown_display)

    rospy.loginfo(
        "display_node started: mode=%s, user_emotion=%s, auto_mode=%s, "
        "idle_sleep=%s (%.1fs) rotate=%.1fs pool=%s, fall_emotion=%s",
        "emotion" if emotion_mode[0] else "oscillogram",
        user_emotion[0],
        auto_mode,
        idle_sleep_enabled,
        idle_sleep_sec,
        idle_sleep_rotate_sec,
        idle_sleep_emotion_pool,
        fall_emotion,
    )
    rospy.spin()


if __name__ == "__main__":
    try:
        main()
    except rospy.ROSInterruptException:
        pass
