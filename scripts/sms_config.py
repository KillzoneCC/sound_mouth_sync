#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sms_config — configuration loader for sound_mouth_sync.

Reads parameters from config/sound_mouth_sync.yaml (loaded into rosparam by
the launch file) and provides accessor functions for each config section.
Allows rosparam overrides via ~param syntax in individual nodes.
"""
from __future__ import annotations

import os
import sys

_yaml_cache = None


def _load_yaml():
    global _yaml_cache
    if _yaml_cache is not None:
        return _yaml_cache
    try:
        import yaml
    except ImportError:
        _yaml_cache = {}
        return _yaml_cache
    paths = []
    try:
        import rospkg
        pkg = rospkg.RosPack().get_path("sound_mouth_sync")
        paths.append(os.path.join(pkg, "config", "sound_mouth_sync.yaml"))
    except Exception:
        pass
    paths.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "config", "sound_mouth_sync.yaml"))
    for p in paths:
        if os.path.isfile(p):
            try:
                with open(p) as f:
                    data = yaml.safe_load(f) or {}
                _yaml_cache = data.get("sound_mouth_sync", data)
                return _yaml_cache
            except Exception:
                pass
    _yaml_cache = {}
    return _yaml_cache


def _get_section(name, defaults=None):
    """Return a config section, merging YAML file with rosparam overrides."""
    cfg = dict(defaults or {})
    yaml_data = _load_yaml()
    if isinstance(yaml_data.get(name), dict):
        cfg.update(yaml_data[name])
    try:
        import rospy
        ns = "/sound_mouth_sync/" + name
        rp = rospy.get_param(ns, {})
        if isinstance(rp, dict):
            cfg.update(rp)
    except Exception:
        pass
    return cfg


def get_hardware_settings():
    return _get_section("hardware", {
        "i2c_port": 1,
        "i2c_address": 0x3D,
        "width": 128,
        "height": 64,
    })


def get_emotions_config():
    return _get_section("emotions", {
        "list": [
            "neutral", "happy", "sad", "angry", "surprised", "excited",
            "sleepy", "love", "confused", "scared", "bored", "calm",
            "disgusted", "tired",
        ],
        "default_emotion": "neutral",
    })


def get_display_settings():
    return _get_section("display", {
        "default_emotion": "neutral",
        "auto_mode": True,
        "silence_return_sec": 3.0,
    })


def get_audio_capture_config():
    return _get_section("audio_capture", {
        "source": "pipewire_monitor",
        "pulse_source": "",
        "device": "plughw:2,0",
        "rate": 16000,
        "chunk_size": 1024,
        "wave_width": 128,
    })
