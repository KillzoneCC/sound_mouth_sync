#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pixel rendering for mouth emotions (SSD1306 1-bit 128x64).

Imported by display_node (compose + I2C) and emotion_node (демо-круг `EMOTION_CYCLE_SEQUENCE` only).
No rospy — keeps tests and offline import clean.

Oscillogram drawing stays in display_node only.
"""
from __future__ import annotations

import logging
import math
import os
import random
import time

log = logging.getLogger(__name__)

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None
    ImageDraw = None
    ImageFont = None

VALID_EMOTIONS = (
    "neutral", "happy", "sad", "angry", "surprised", "excited",
    "sleepy", "sleep", "love", "cute", "confused", "scared", "bored", "calm",
    "disgusted", "tired", "cat",
)

# Порядок для демо-круга в emotion_node (~emotion_cycle_enabled).
# Каждое имя ∈ VALID_EMOTIONS (иначе display откатит нормализацию к neutral).
EMOTION_CYCLE_SEQUENCE = (
    "neutral",
    "happy",
    "sad",
    "angry",
    "surprised",
    "excited",
    "love",
    "cute",
    "confused",
    "scared",
    "bored",
    "calm",
    "disgusted",
    "tired",
    "sleepy",
    "sleep",
    "cat",
)

FALL_POSTURES = frozenset(
    ("fall_forward", "fall_backward", "fall_left", "fall_right"),
)

_CAT_ANIM_PERIOD_SEC = 0.5
_SLEEP_ANIM_PERIOD_SEC = 0.5

_BUILTIN_IDLE_ANIM_NAMES = ("cigarette", "cat", "yawn_zzz")

_SLEEPY_FONT_CACHE = {}


def sleepy_font(size_px):
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


def load_idle_faces(resources_dir, width, height):
    """Same contract as former display_node._load_idle_faces."""
    if not Image:
        return []
    faces_dir = os.path.join(resources_dir, "idle_faces")
    if not os.path.isdir(faces_dir):
        return []
    result = []
    default_ms = 120

    for entry in sorted(os.listdir(faces_dir)):
        path = os.path.join(faces_dir, entry)

        if entry.lower().endswith(".gif") and os.path.isfile(path):
            try:
                gif = Image.open(path)
                frames = []
                durations = []
                for frame_idx in range(getattr(gif, "n_frames", 1)):
                    gif.seek(frame_idx)
                    frame = gif.copy().convert("1").resize((width, height))
                    frames.append(frame)
                    dur = gif.info.get("duration", default_ms)
                    durations.append(max(30, int(dur)) if dur else default_ms)
                if len(frames) >= 2:
                    name = os.path.splitext(entry)[0]
                    result.append(
                        {"name": name, "frames": frames, "durations": durations})
            except Exception:
                pass

        elif os.path.isdir(path) and not entry.startswith("."):
            pngs = sorted(
                f for f in os.listdir(path)
                if f.lower().endswith((".png", ".bmp"))
            )
            frames = []
            for png_name in pngs:
                try:
                    img = Image.open(os.path.join(path, png_name)).convert(
                        "1").resize((width, height))
                    frames.append(img)
                except Exception:
                    pass
            if len(frames) >= 2:
                result.append({
                    "name": entry,
                    "frames": frames,
                    "durations": [default_ms] * len(frames),
                })

    return result


def draw_builtin_idle_cat(width, height, elapsed):
    img = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(img)
    lw = 2
    frame_sec = 0.5
    phase = int(elapsed / frame_sec) % 2

    draw.polygon([(59, 29), (69, 29), (64, 37)], fill=255, outline=255)

    left_base = [(51, 31), (50, 34), (51, 37)]
    right_base = [(77, 31), (78, 34), (77, 37)]

    if phase == 0:
        left_tip = [(22, 25), (18, 34), (22, 41)]
        right_tip = [(106, 25), (110, 34), (106, 41)]
    else:
        left_tip = [(24, 29), (19, 40), (24, 47)]
        right_tip = [(104, 29), (109, 40), (104, 47)]

    for b, t in zip(left_base, left_tip):
        draw.line([b, t], fill=255, width=lw)
    for b, t in zip(right_base, right_tip):
        draw.line([b, t], fill=255, width=lw)

    return img


def draw_builtin_idle_yawn_zzz(width, height, elapsed):
    img = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(img)
    cx, cy = width // 2, height // 2

    frame_sec = 0.8
    phase = int(elapsed / frame_sec) % 3

    mouth_cx = cx - 16
    mouth_cy = cy + 2

    if phase == 0:
        r = 10
        draw.ellipse((mouth_cx - r, mouth_cy - r, mouth_cx +
                     r, mouth_cy + r), outline=255, width=2)
    elif phase == 1:
        r = 14
        draw.ellipse((mouth_cx - r, mouth_cy - r + 2, mouth_cx +
                     r, mouth_cy + r + 2), outline=255, width=2)
    else:
        r = 8
        draw.ellipse((mouth_cx - r, mouth_cy - r, mouth_cx +
                     r, mouth_cy + r), outline=255, width=2)

    zzz_x_base = cx + 10
    zzz_y_base = cy - 8

    cycle = elapsed % 3.0
    for i in range(3):
        letter_age = (cycle - i * 0.4) % 3.0
        if letter_age > 2.0:
            continue
        frac = letter_age / 2.0
        lx = zzz_x_base + int(i * 12 + frac * 8)
        ly = zzz_y_base - int(frac * 20)
        sz = max(8, int(14 - frac * 6))
        f = sleepy_font(sz)
        draw.text((lx, ly), "Z", fill=255, font=f)

    return img


def sleepy_anim_reset(state):
    state.clear()
    state["start"] = time.time()
    state["puffs"] = []
    state["next_spawn"] = 0.0


def draw_sleepy_animated(width, height, state):
    if "start" not in state:
        sleepy_anim_reset(state)

    img = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(img)

    t0 = state["start"]
    elapsed = time.time() - t0

    sx = float(width) / 128.0
    sy = float(height) / 64.0
    s = min(sx, sy)
    lw = max(1, int(round(2 * s)))

    def _sxy(x, y):
        return (int(round(x * sx)), int(round(y * sy)))

    jiggle_x = int(round(math.sin(elapsed * 1.1) * 0.6 * sx))
    jiggle_y = int(round(math.cos(elapsed * 0.9) * 0.4 * sy))

    ember_rect = (16, 46, 28, 56)
    body1 = (26, 50, 92, 42)
    body2 = (26, 54, 94, 46)
    filter_line = (88, 40, 88, 48)
    smoke1 = [(24, 46), (18, 38), (22, 30),
              (16, 22), (20, 14), (14, 8), (18, 4)]
    smoke2 = [(28, 44), (32, 34), (26, 26), (30, 18), (24, 10), (28, 5)]

    ex0, ey0 = _sxy(ember_rect[0], ember_rect[1])
    ex1, ey1 = _sxy(ember_rect[2], ember_rect[3])
    draw.ellipse((ex0 + jiggle_x, ey0 + jiggle_y, ex1 + jiggle_x,
                 ey1 + jiggle_y), outline=255, width=lw)

    bx00, by00 = _sxy(body1[0], body1[1])
    bx01, by01 = _sxy(body1[2], body1[3])
    bx10, by10 = _sxy(body2[0], body2[1])
    bx11, by11 = _sxy(body2[2], body2[3])
    fx0, fy0 = _sxy(filter_line[0], filter_line[1])
    fx1, fy1 = _sxy(filter_line[2], filter_line[3])

    draw.line((bx00 + jiggle_x, by00 + jiggle_y, bx01 +
              jiggle_x, by01 + jiggle_y), fill=255, width=lw)
    draw.line((bx10 + jiggle_x, by10 + jiggle_y, bx11 +
              jiggle_x, by11 + jiggle_y), fill=255, width=lw)
    draw.line((fx0 + jiggle_x, fy0 + jiggle_y, fx1 + jiggle_x,
              fy1 + jiggle_y), fill=255, width=max(1, lw - 1))

    flicker = math.sin(elapsed * 11.0) * 0.5 + 0.5
    ember_on = flicker > 0.55 or random.random() < 0.08
    if ember_on:
        hot_x, hot_y = _sxy(20, 47)
        draw.ellipse(
            (hot_x + jiggle_x - 1, hot_y + jiggle_y - 1,
             hot_x + jiggle_x + 1, hot_y + jiggle_y + 1),
            fill=255,
            outline=255,
        )

    if elapsed >= float(state.get("next_spawn", 0.0)):
        state["puffs"].append({
            "born": elapsed,
            "life": random.uniform(0.85, 1.25),
            "speed": random.uniform(18.0, 26.0) * sy,
            "drift": random.uniform(-6.0, 6.0) * sx,
            "phase": random.uniform(0.0, math.tau),
            "wiggle": random.uniform(0.8, 1.6) * s,
        })
        state["puffs"] = state["puffs"][-6:]
        state["next_spawn"] = elapsed + random.uniform(0.28, 0.48)

    smoke1_f = [(x * sx, y * sy) for (x, y) in smoke1]
    smoke2_f = [(x * sx, y * sy) for (x, y) in smoke2]

    new_puffs = []
    for puff in state["puffs"]:
        age = elapsed - puff["born"]
        if age < 0.0:
            new_puffs.append(puff)
            continue
        if age > puff["life"]:
            continue

        dy = -age * puff["speed"]
        dx = puff["drift"] * math.sin(age * 2.4 + puff["phase"])

        w = max(1, int(round(lw - age * 0.9)))
        step = 2 if age > (puff["life"] * 0.7) else 1

        pts1 = []
        for i in range(0, len(smoke1_f), step):
            x, y = smoke1_f[i]
            wig = puff["wiggle"] * \
                math.sin(age * 6.0 + puff["phase"] + i * 0.7)
            py = y + math.cos(age * 5.0 +
                              puff["phase"] + i) * (0.15 * puff["wiggle"])
            pts1.append((int(round(x + dx + jiggle_x + wig * 0.6)),
                        int(round(py + dy + jiggle_y))))
        if len(pts1) >= 2:
            draw.line(pts1, fill=255, width=w)

        pts2 = []
        for i in range(0, len(smoke2_f), step):
            x, y = smoke2_f[i]
            wig = puff["wiggle"] * \
                math.cos(age * 5.5 + puff["phase"] + i * 0.6)
            py = y + math.sin(age * 4.8 +
                              puff["phase"] + i) * (0.12 * puff["wiggle"])
            pts2.append((int(round(x + dx + jiggle_x + wig * 0.6)),
                        int(round(py + dy + jiggle_y))))
        if len(pts2) >= 2:
            draw.line(pts2, fill=255, width=w)

        new_puffs.append(puff)
    state["puffs"] = new_puffs

    return img


def draw_emotion_angry_fall(width, height):
    img = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(img)
    cx, cy = width // 2, height // 2
    mx, my = cx, cy
    lw = 3

    draw.line((mx - 20, my - 5, mx + 20, my - 5), fill=255, width=lw)
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


def load_custom_emotion_image(emotion, resources_dir, width, height):
    if not Image:
        return None
    for ext in (".png", ".bmp", ".gif"):
        path = os.path.join(resources_dir, "emotions", emotion + ext)
        if os.path.isfile(path):
            try:
                img = Image.open(path).convert("1").resize((width, height))
                return img
            except Exception as e:
                log.warning(
                    "mouth_emotion_render: failed to load %s: %s", path, e)
    return None


def draw_emotion(emotion, width, height):
    if not Image:
        return None
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
        draw.arc((cx - r, cy - r, cx + r, cy + r),
                 180, 360, fill=255, width=lw)
    elif emotion == "angry":
        draw.line((cx - r, cy, cx + r, cy), fill=255, width=lw)
    elif emotion == "surprised":
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=255, width=lw)
    elif emotion == "excited":
        draw.arc((cx - r, cy - r, cx + r, cy + r), 0, 180, fill=255, width=lw)
    elif emotion == "tired":
        # Короткий приплюснутый рот + лёгкие «мешки»/намёк на веки — не путать с bored (широкая линия).
        rs_m = max(5, r // 3)
        lw_t = max(1, lw - 1)
        my = cy + 2
        draw.line((cx - rs_m, my, cx + rs_m, my), fill=255, width=lw_t)
        le = max(4, r // 4)
        draw.line((cx - r // 2, cy - 10, cx - r //
                  2 - le, cy - 5), fill=255, width=1)
        draw.line((cx + r // 2, cy - 10, cx + r //
                  2 + le, cy - 5), fill=255, width=1)
    elif emotion == "sleepy":
        sx = float(width) / 128.0
        sy = float(height) / 64.0
        s = min(sx, sy)
        lw_s = max(1, int(round(2 * s)))

        def _sxy(x, y):
            return (int(round(x * sx)), int(round(y * sy)))

        ember_rect = (16, 46, 28, 56)
        body1 = (26, 50, 92, 42)
        body2 = (26, 54, 94, 46)
        filter_line = (88, 40, 88, 48)
        smoke1 = [(24, 46), (18, 38), (22, 30),
                  (16, 22), (20, 14), (14, 8), (18, 4)]
        smoke2 = [(28, 44), (32, 34), (26, 26), (30, 18), (24, 10), (28, 5)]

        ex0, ey0 = _sxy(ember_rect[0], ember_rect[1])
        ex1, ey1 = _sxy(ember_rect[2], ember_rect[3])
        draw.ellipse((ex0, ey0, ex1, ey1), outline=255, width=lw_s)

        bx00, by00 = _sxy(body1[0], body1[1])
        bx01, by01 = _sxy(body1[2], body1[3])
        bx10, by10 = _sxy(body2[0], body2[1])
        bx11, by11 = _sxy(body2[2], body2[3])
        fx0, fy0 = _sxy(filter_line[0], filter_line[1])
        fx1, fy1 = _sxy(filter_line[2], filter_line[3])

        draw.line((bx00, by00, bx01, by01), fill=255, width=lw_s)
        draw.line((bx10, by10, bx11, by11), fill=255, width=lw_s)
        draw.line((fx0, fy0, fx1, fy1), fill=255, width=max(1, lw_s - 1))

        hot_x, hot_y = _sxy(20, 47)
        draw.ellipse((hot_x - 1, hot_y - 1, hot_x + 1,
                     hot_y + 1), fill=255, outline=255)

        draw.line([_sxy(x, y) for (x, y) in smoke1],
                  fill=255, width=max(1, lw_s - 1))
        draw.line([_sxy(x, y) for (x, y) in smoke2],
                  fill=255, width=max(1, lw_s - 1))
    elif emotion == "love":
        draw.arc((cx - r, cy - r - 2, cx + r, cy + r - 2),
                 0, 180, fill=255, width=lw)
    elif emotion == "cute":
        # Fallback if resources/emotions/cute.* отсутствует: упрощённый «uwu»
        ro = 7
        draw.ellipse((cx - 38 - ro, cy - 6 - ro, cx - 38 + ro, cy - 6 + ro),
                     outline=255, width=2)
        draw.ellipse((cx + 38 - ro, cy - 6 - ro, cx + 38 + ro, cy - 6 + ro),
                     outline=255, width=2)
        for dx in (-38, 38):
            for k in range(3):
                ox = dx + (k - 1) * 3
                draw.line((ox - 4, cy - 9 + k * 2, ox + 4, cy - 3 + k * 2),
                          fill=255, width=1)
        w = max(8, r // 2)
        draw.arc((cx - w, cy - 2, cx, cy + 10), 200, 340, fill=255, width=2)
        draw.arc((cx, cy - 2, cx + w, cy + 10), 200, 340, fill=255, width=2)
    elif emotion == "confused":
        n = 9
        pts = [(cx - r + (2 * r * i) // (n - 1), cy + (6 if i % 2 else -6))
               for i in range(n)]
        for i in range(len(pts) - 1):
            draw.line([pts[i], pts[i + 1]], fill=255, width=lw)
    elif emotion == "scared":
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=255, width=lw)
    elif emotion == "bored":
        # Широкая ровная линия «скука» — длиннее, чем у tired (короткий рот).
        rs_b = max(10, (2 * r) // 3)
        draw.line((cx - rs_b, cy, cx + rs_b, cy), fill=255, width=lw)
    elif emotion == "calm":
        draw.arc((cx - r, cy - r, cx + r, cy + r), 30, 150, fill=255, width=lw)
    elif emotion == "disgusted":
        draw.arc((cx - r, cy - r, cx + r, cy + r),
                 180, 360, fill=255, width=lw)
    else:
        draw.line((cx - r, cy, cx + r, cy), fill=255, width=lw)
    return img


def cat_animation_bitmap(cat_frames):
    n = len(cat_frames)
    if n >= 2:
        idx = int(time.time() / _CAT_ANIM_PERIOD_SEC) % n
        return cat_frames[idx]
    if n == 1:
        return cat_frames[0]
    return None


def sleep_animation_bitmap(sleep_frames):
    n = len(sleep_frames)
    if n >= 2:
        idx = int(time.time() / _SLEEP_ANIM_PERIOD_SEC) % n
        return sleep_frames[idx]
    if n == 1:
        return sleep_frames[0]
    return None


def idle_face_pick_random(idle_faces, idle_face_state, builtin_idle_state, sleepy_state):
    if idle_faces:
        if len(idle_faces) == 1:
            idle_face_state["anim_idx"] = 0
        else:
            prev = idle_face_state["anim_idx"]
            choices = [i for i in range(len(idle_faces)) if i != prev]
            idle_face_state["anim_idx"] = random.choice(choices)
        idle_face_state["frame_idx"] = 0
        idle_face_state["last_frame_time"] = time.time()
    else:
        n_builtins = len(_BUILTIN_IDLE_ANIM_NAMES)
        prev = builtin_idle_state["anim_idx"]
        choices = [i for i in range(n_builtins) if i != prev]
        builtin_idle_state["anim_idx"] = random.choice(
            choices) if choices else 0
        builtin_idle_state["start"] = time.time()
        sleepy_anim_reset(sleepy_state)


def idle_face_get_frame(idle_faces, idle_face_state):
    idx = idle_face_state["anim_idx"]
    if idx < 0 or idx >= len(idle_faces):
        return None
    anim = idle_faces[idx]
    frames = anim["frames"]
    durations = anim["durations"]
    fi = idle_face_state["frame_idx"] % len(frames)

    now = time.time()
    dur_sec = durations[fi] / 1000.0
    if (now - idle_face_state["last_frame_time"]) >= dur_sec:
        fi = (fi + 1) % len(frames)
        idle_face_state["frame_idx"] = fi
        idle_face_state["last_frame_time"] = now

    return frames[fi]


def builtin_idle_get_frame(width, height, builtin_idle_state, sleepy_state):
    idx = builtin_idle_state["anim_idx"]
    if idx < 0 or idx >= len(_BUILTIN_IDLE_ANIM_NAMES):
        return None
    elapsed = time.time() - builtin_idle_state["start"]
    name = _BUILTIN_IDLE_ANIM_NAMES[idx]
    if name == "cigarette":
        return draw_sleepy_animated(width, height, sleepy_state)
    if name == "cat":
        return draw_builtin_idle_cat(width, height, elapsed)
    if name == "yawn_zzz":
        return draw_builtin_idle_yawn_zzz(width, height, elapsed)
    return None


def compose_emotion_frame(
    emo,
    width,
    height,
    *,
    custom_emotion_cache,
    cat_animation_frames,
    sleep_animation_frames,
    idle_faces,
    idle_sleep_active,
    robot_fallen,
    fall_emotion,
    idle_face_state,
    builtin_idle_state,
    sleepy_state,
):
    """Build 1-bit PIL image for the mouth; same rules as former display_node._compose_emotion_frame."""
    if not Image:
        return None
    emo = (emo or "neutral").strip().lower()
    if emo == "cat" and cat_animation_frames:
        cat_img = cat_animation_bitmap(cat_animation_frames)
        if cat_img is not None:
            return cat_img
    if emo == "sleep" and sleep_animation_frames:
        sleep_img = sleep_animation_bitmap(sleep_animation_frames)
        if sleep_img is not None:
            return sleep_img
    cached = custom_emotion_cache.get(emo)
    if cached is not None:
        return cached
    if robot_fallen and emo == fall_emotion:
        if emo == "angry" and "angry" not in custom_emotion_cache:
            return draw_emotion_angry_fall(width, height)
        return draw_emotion(emo, width, height)
    if idle_sleep_active:
        if idle_faces:
            frame = idle_face_get_frame(idle_faces, idle_face_state)
            if frame is not None:
                return frame
        else:
            if emo == "sleepy":
                if custom_emotion_cache.get("sleepy"):
                    return custom_emotion_cache["sleepy"]
                return draw_sleepy_animated(width, height, sleepy_state)
            if emo == "cat":
                elapsed = time.time() - float(builtin_idle_state.get("start", 0.0) or 0.0)
                return draw_builtin_idle_cat(width, height, elapsed)
            if emo == "sleep":
                elapsed = time.time() - float(builtin_idle_state.get("start", 0.0) or 0.0)
                return draw_builtin_idle_yawn_zzz(width, height, elapsed)
            frame = builtin_idle_get_frame(
                width, height, builtin_idle_state, sleepy_state)
            if frame is not None:
                return frame
    if emo == "sleepy":
        return draw_sleepy_animated(width, height, sleepy_state)
    if emo == "cat":
        elapsed = time.time() - float(builtin_idle_state.get("start", 0.0) or 0.0)
        return draw_builtin_idle_cat(width, height, elapsed)
    if emo == "sleep":
        elapsed = time.time() - float(builtin_idle_state.get("start", 0.0) or 0.0)
        return draw_builtin_idle_yawn_zzz(width, height, elapsed)
    return draw_emotion(emo, width, height)
