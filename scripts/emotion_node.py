#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
emotion_node — stores and republishes effective mouth emotion/mode.

Pixel drawing for emotions lives in mouth_emotion_render.py; display_node imports
that module to push frames to the OLED. This node does not render pixels.

Goal:
  Keep external API stable (/mouth/mode, /mouth/emotion) while decoupling
  emotion command storage from display_node.

Input topics (external API, unchanged):
  /mouth/mode
  /mouth/emotion

Output topics (internal effective state):
  /mouth/effective_mode
  /mouth/effective_emotion

Optional demo (parameters):
  ~emotion_cycle_enabled   if true, cyclically republish /mouth/effective_emotion
                            using mouth_emotion_render.EMOTION_CYCLE_SEQUENCE
  ~emotion_cycle_interval_sec  seconds between steps (default 3.0)

  The cycle advances only while effective mode is "emotion" (oscillogram pauses
  the loop without advancing the index). When the cycle is enabled, the node
  forces initial /mouth/effective_mode to "emotion" so one terminal is enough.

  Stop with Ctrl+C like any ROS node.
"""
from __future__ import annotations

import os
import sys

import rospy
from std_msgs.msg import String

try:
    import rospkg

    _pkg_path = rospkg.RosPack().get_path("sound_mouth_sync")
    sys.path.insert(0, os.path.join(_pkg_path, "scripts"))
except Exception:
    pass

_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

import mouth_emotion_render as mer
import sms_config


def _normalize_mode(raw: str, default_mode: str) -> str:
    m = (raw or "").strip().lower()
    if m in ("emotion", "oscillogram"):
        return m
    return default_mode


def _normalize_emotion(raw: str, default_emotion: str) -> str:
    # Keep pass-through for custom emotions (only normalize format).
    e = (raw or "").strip().lower()
    return e or default_emotion


def main():
    rospy.init_node("mouth_emotion_node")

    display_cfg = sms_config.get_display_settings()
    emotions_cfg = sms_config.get_emotions_config()

    default_emotion = rospy.get_param(
        "~default_emotion",
        emotions_cfg.get("default_emotion", "neutral"),
    )
    default_mode = rospy.get_param(
        "~default_mode",
        display_cfg.get("start_display_mode", "oscillogram"),
    )
    default_mode = _normalize_mode(default_mode, "oscillogram")
    default_emotion = _normalize_emotion(default_emotion, "neutral")

    emotion_cycle_enabled = bool(
        rospy.get_param("~emotion_cycle_enabled", False))

    in_mode_topic = rospy.get_param("~input_mode_topic", "/mouth/mode")
    in_emotion_topic = rospy.get_param("~input_emotion_topic", "/mouth/emotion")
    out_mode_topic = rospy.get_param("~output_mode_topic", "/mouth/effective_mode")
    out_emotion_topic = rospy.get_param("~output_emotion_topic", "/mouth/effective_emotion")

    current_mode = [default_mode]
    current_emotion = [default_emotion]
    if emotion_cycle_enabled:
        current_mode[0] = "emotion"

    mode_pub = rospy.Publisher(out_mode_topic, String, queue_size=1, latch=True)
    emotion_pub = rospy.Publisher(out_emotion_topic, String, queue_size=1, latch=True)

    def publish_all():
        mode_pub.publish(String(data=current_mode[0]))
        emotion_pub.publish(String(data=current_emotion[0]))

    def on_mode(msg: String):
        new_mode = _normalize_mode(msg.data, current_mode[0])
        if new_mode != current_mode[0]:
            current_mode[0] = new_mode
            mode_pub.publish(String(data=current_mode[0]))

    def on_emotion(msg: String):
        new_emo = _normalize_emotion(msg.data, current_emotion[0])
        if new_emo != current_emotion[0]:
            current_emotion[0] = new_emo
            emotion_pub.publish(String(data=current_emotion[0]))

    rospy.Subscriber(in_mode_topic, String, on_mode, queue_size=1)
    rospy.Subscriber(in_emotion_topic, String, on_emotion, queue_size=1)

    publish_all()

    emotion_cycle_interval_sec = float(
        rospy.get_param("~emotion_cycle_interval_sec", 3.0))
    emotion_cycle_interval_sec = max(0.3, emotion_cycle_interval_sec)

    if emotion_cycle_enabled:
        sequence = tuple(mer.EMOTION_CYCLE_SEQUENCE)
        if not sequence:
            rospy.logwarn("emotion_node: EMOTION_CYCLE_SEQUENCE empty, cycle disabled")
        else:
            bad = [x for x in sequence if x not in mer.VALID_EMOTIONS]
            if bad:
                rospy.logwarn(
                    "emotion_node: EMOTION_CYCLE_SEQUENCE has invalid ids %s (skipped entries)",
                    bad,
                )
            sequence = tuple(x for x in sequence if x in mer.VALID_EMOTIONS)
        if emotion_cycle_enabled and sequence:
            cycle_idx = [0]
            if default_emotion in sequence:
                cycle_idx[0] = sequence.index(default_emotion)

            def _cycle_tick(_evt):
                if current_mode[0] != "emotion":
                    return
                cycle_idx[0] = (cycle_idx[0] + 1) % len(sequence)
                current_emotion[0] = sequence[cycle_idx[0]]
                emotion_pub.publish(String(data=current_emotion[0]))
                rospy.loginfo("emotion_node: cycle %d/%d -> %s",
                              cycle_idx[0] + 1, len(sequence), current_emotion[0])

            rospy.Timer(rospy.Duration(emotion_cycle_interval_sec), _cycle_tick)
            rospy.loginfo(
                "emotion_node: emotion cycle ON (interval=%.1fs, %d steps); "
                "only runs in mode=emotion; Ctrl+C to exit",
                emotion_cycle_interval_sec,
                len(sequence),
            )
        elif emotion_cycle_enabled:
            rospy.logwarn(
                "emotion_node: emotion cycle requested but sequence is empty after validation")

    reassert_sec = float(
        rospy.get_param(
            "~reassert_effective_topics_after_sec",
            display_cfg.get("reassert_effective_topics_after_sec", 0.0),
        )
    )
    if reassert_sec > 0:

        def _reassert_effective(_evt):
            publish_all()
            rospy.loginfo(
                "emotion_node: reasserted effective mode/emotion after %.1fs",
                reassert_sec,
            )

        rospy.Timer(rospy.Duration(reassert_sec), _reassert_effective, oneshot=True)

    rospy.loginfo(
        "emotion_node started: in(mode=%s, emotion=%s) -> out(mode=%s, emotion=%s), defaults: mode=%s emotion=%s",
        in_mode_topic,
        in_emotion_topic,
        out_mode_topic,
        out_emotion_topic,
        current_mode[0],
        current_emotion[0],
    )
    rospy.spin()


if __name__ == "__main__":
    try:
        main()
    except rospy.ROSInterruptException:
        pass

