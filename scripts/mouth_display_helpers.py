#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Non-node helpers for display_node: standup wait, I2C probe, oscillogram bitmap,
emotion asset preload, OLED driver string normalization.

Keeps display_node.py smaller without changing behaviour (no rospy.Node here).
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import rospy

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None
    ImageDraw = None

# OLED 0x3D ownership (motik vs sound_mouth_sync) — must match display_node contract
DRIVER_TOPIC = "/oled_3d/active_driver"
DRIVER_MOUTH = "sound_mouth_sync"
DRIVER_MOTIK = "motik"


def normalize_oled_driver(raw: Optional[str]) -> str:
    s = (raw or "").strip().lower()
    if s in ("sound_mouth_sync", "mouth", "sound_sync", ""):
        return DRIVER_MOUTH
    if s == "motik":
        return DRIVER_MOTIK
    return DRIVER_MOUTH


def draw_oscillogram_waveform(y_values, width: int, height: int):
    """Draw an oscillogram line from W values in -1..1. Returns PIL Image or None if no PIL."""
    if Image is None or ImageDraw is None:
        return None
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


def wait_for_robot_standup(log_prefix: str = "mouth_display_node") -> bool:
    """
    Wait for init_pose/init_finish=True before opening mouth OLED.

    ~wait_standup_timeout_sec: 0 = unlimited; >0 = seconds.
    ~proceed_without_standup: if timeout >0 and true, continue without standup.
    """
    timeout = float(rospy.get_param("~wait_standup_timeout_sec", 0.0))
    _proceed = rospy.get_param("~proceed_without_standup", False)
    proceed = _proceed is True or (
        isinstance(_proceed, str) and _proceed.strip().lower() in ("true", "1", "yes")
    )
    start = time.time()
    while not rospy.is_shutdown():
        if rospy.get_param("init_pose/init_finish", False):
            rospy.loginfo(
                "%s: робот встал (init_pose/init_finish), открываем OLED", log_prefix
            )
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


def i2c_addr_seen_on_bus(bus_nr: int, addr: int):
    """
    True if i2cdetect reports addr on bus_nr.
    None if i2cdetect missing or failed (same idea as ainex_bringup oled_display.py).
    """
    try:
        out = subprocess.check_output(
            ["i2cdetect", "-y", str(bus_nr)],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=8,
        )
    except FileNotFoundError:
        return None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    tag = format(addr, "02x").lower()
    return bool(re.search(r"(?<![0-9a-fA-F])" + re.escape(tag) + r"(?![0-9a-fA-F])", out))


def wait_mouth_oled_on_bus(log_prefix: str, bus_nr: int, mouth_addr: int, max_wait_sec: float) -> bool:
    """Poll I2C until mouth SSD1306 ACKs at mouth_addr or timeout."""
    if max_wait_sec <= 0:
        return True
    t0 = time.time()
    time.sleep(0.25)
    first = i2c_addr_seen_on_bus(bus_nr, mouth_addr)
    if first is None:
        rospy.logwarn(
            "%s: i2cdetect not available — sleeping %.1fs before opening mouth OLED "
            "(install i2c-tools for I2C probe on cold boot)",
            log_prefix,
            max_wait_sec,
        )
        rospy.sleep(max_wait_sec)
        return True
    if first:
        rospy.loginfo(
            "%s: mouth OLED visible at I2C 0x%02X (%.2fs)",
            log_prefix,
            mouth_addr,
            time.time() - t0,
        )
        return True
    rospy.loginfo(
        "%s: polling bus %d for I2C 0x%02X (max %.1fs, cold/slow power)",
        log_prefix,
        bus_nr,
        mouth_addr,
        max_wait_sec,
    )
    deadline = t0 + max_wait_sec
    while time.time() < deadline and not rospy.is_shutdown():
        if i2c_addr_seen_on_bus(bus_nr, mouth_addr):
            rospy.loginfo(
                "%s: mouth OLED at I2C 0x%02X after %.2fs",
                log_prefix,
                mouth_addr,
                time.time() - t0,
            )
            return True
        rospy.sleep(0.3)
    seen_3c = i2c_addr_seen_on_bus(bus_nr, 0x3C)
    seen_3d = i2c_addr_seen_on_bus(bus_nr, mouth_addr)
    rospy.logwarn(
        "%s: timeout waiting for I2C 0x%02X — check cable, power, ADDR strap (mouth must be 0x3D)",
        log_prefix,
        mouth_addr,
    )
    if seen_3c is True and seen_3d is False:
        rospy.logerr(
            "%s: DIAGNOSTIC: 0x3C present, 0x%02X absent. oled_display writes stats only to 0x3C; "
            "if the mouth panel shows SSID/IP, both SSD1306 modules likely listen on 0x3C — "
            "set mouth module ADDR to 0x3D and run i2cdetect -y %d (expect 3c and 3d).",
            log_prefix,
            mouth_addr,
            bus_nr,
        )
    return False


def make_norm_emotion(custom_emotion_cache: Dict[str, Any], mer) -> Callable[[str], str]:
    """Normalize emotion name using VALID_EMOTIONS and loaded custom assets."""

    def _norm_emotion(name: str) -> str:
        raw = (name or "neutral").strip().lower() or "neutral"
        if raw not in mer.VALID_EMOTIONS and raw not in custom_emotion_cache:
            return "neutral"
        return raw

    return _norm_emotion


def resolve_idle_sleep_emotion_pool(
    display_cfg: dict, norm_emotion: Callable[[str], str]
) -> List[str]:
    """Read ~idle_sleep_emotions / ~idle_sleep_emotion / YAML; return non-empty pool."""
    _idle_pool_raw = rospy.get_param(
        "~idle_sleep_emotions", display_cfg.get("idle_sleep_emotions")
    )
    if _idle_pool_raw is None:
        _idle_pool_raw = rospy.get_param(
            "~idle_sleep_emotion", display_cfg.get("idle_sleep_emotion", "sleepy")
        )
    if isinstance(_idle_pool_raw, str):
        _idle_pool_raw = [_idle_pool_raw]
    elif not isinstance(_idle_pool_raw, (list, tuple)):
        _idle_pool_raw = ["sleepy"]
    pool = [norm_emotion(str(x).strip()) for x in _idle_pool_raw if str(x).strip()]
    if not pool:
        pool = ["sleepy"]
    return pool


def preload_emotion_assets(
    resources_dir: str,
    width: int,
    height: int,
    mer,
) -> Tuple[Dict[str, Any], List[Any], List[Any], List[dict]]:
    """
    Load custom emotions/, cat/sleep frame strips, idle_faces/. Same logic as former inline block.

    Returns:
      custom_emotion_cache, cat_animation_frames, sleep_animation_frames, idle_faces
    """
    custom_emotion_cache: Dict[str, Any] = {}
    if Image:
        emo_dir = os.path.join(resources_dir, "emotions")
        if os.path.isdir(emo_dir):
            for fname in os.listdir(emo_dir):
                name, ext = os.path.splitext(fname)
                if ext.lower() in (".png", ".bmp", ".gif"):
                    img = mer.load_custom_emotion_image(name, resources_dir, width, height)
                    if img:
                        custom_emotion_cache[name.lower()] = img
                        rospy.loginfo(
                            "display_node: loaded custom emotion '%s' from %s", name, fname
                        )

    cat_animation_frames: List[Any] = []
    for key in ("cat_frame0", "cat_frame1"):
        cimg = mer.load_custom_emotion_image(key, resources_dir, width, height)
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

    sleep_animation_frames: List[Any] = []
    for key in ("sleep_frame1", "sleep_frame2", "sleep_frame3"):
        simg = mer.load_custom_emotion_image(key, resources_dir, width, height)
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

    idle_faces = mer.load_idle_faces(resources_dir, width, height)
    if idle_faces:
        rospy.loginfo(
            "display_node: loaded %d idle face animation(s): %s",
            len(idle_faces),
            ", ".join(f["name"] for f in idle_faces),
        )
    else:
        rospy.loginfo(
            "display_node: no idle face animations in resources/idle_faces/ — "
            "using 3 built-in idle anims: %s",
            ", ".join(mer._BUILTIN_IDLE_ANIM_NAMES),
        )

    return custom_emotion_cache, cat_animation_frames, sleep_animation_frames, idle_faces
