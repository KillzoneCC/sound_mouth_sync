# sound_mouth_sync — AI Developer Context

> **MANDATORY:** Any AI agent modifying this package MUST update this file to reflect
> the changes made. This document is the single source of truth for AI-assisted
> development on this package.

## Purpose

`sound_mouth_sync` manages the mouth OLED display (SSD1306 128x64, I2C 0x3D) on the
Ainex humanoid robot. It renders facial expressions (emotions) and real-time audio
oscillograms.

## Rules for AI Agents

1. **Update this file** after every change — new topics, parameters, files, or
   behavioural changes.
2. **Do not hardcode audio file paths.** The audio_capture_node captures ALL system
   audio output. No specific file playback.
3. **Do not touch ainex_bringup.** This package is self-contained. Integration is
   via the launch include in `ainex_bringup/launch/bringup.launch`.
4. **Preserve topic names.** Other packages depend on `/mouth/mode`,
   `/mouth/emotion`, `/mouth/audio_wave`, `/mouth/current_mode`,
   `/mouth/current_emotion`, `/audio/level`.
5. **Test with rostopic pub.** See README.md for examples before and after changes.
6. **Custom emotions** are PNG files in `resources/emotions/`. Name = emotion name
   (lowercase). 128x64 pixels, 1-bit (black & white).
7. **Config changes** go in `config/sound_mouth_sync.yaml` and must be reflected in
   `sms_config.py` accessor functions.

## File Map

| File | Role | Key Details |
|------|------|-------------|
| `scripts/display_node.py` | Node 1: OLED display | Subscribes `/mouth/mode`, `/mouth/emotion`, `/mouth/audio_wave`. Publishes `/mouth/current_mode`, `/mouth/current_emotion`. Draws on OLED 0x3D via luma.oled. |
| `scripts/audio_capture_node.py` | Node 2: audio capture | Captures system audio (PipeWire/Pulse/ALSA). Publishes `/mouth/audio_wave` (Float32MultiArray, 128 pts) and `/audio/level` (Float32). |
| `scripts/sms_config.py` | Config module | Loads `config/sound_mouth_sync.yaml` + rosparam overrides. Used by both nodes. |
| `config/sound_mouth_sync.yaml` | Parameters | emotions list, display settings, audio capture settings, hardware (I2C). |
| `launch/sound_mouth_sync.launch` | Launch file | Loads config, starts both nodes with args. |
| `resources/emotions/` | Custom PNGs | 128x64 1-bit. Loaded at node startup. Name = emotion name. |
| `README.md` | User docs | Examples, installation, parameters. |
| `doc/ARCHITECTURE.md` | Architecture | Mermaid diagrams, node descriptions, data flow. |
| `doc/AI_CONTEXT.md` | This file | AI agent rules, full context. |

## Topics

### Subscribed by display_node

| Topic | Type | Values | Source |
|-------|------|--------|--------|
| `/mouth/mode` | `String` | `"emotion"`, `"oscillogram"` | Any external node |
| `/mouth/emotion` | `String` | Any from VALID_EMOTIONS or custom PNG name | Any external node |
| `/mouth/audio_wave` | `Float32MultiArray` | 128 floats in [-1, 1] | audio_capture_node |

### Published by display_node

| Topic | Type | Latch | Description |
|-------|------|-------|-------------|
| `/mouth/current_mode` | `String` | Yes | Current display mode |
| `/mouth/current_emotion` | `String` | Yes | Current emotion name |

### Published by audio_capture_node

| Topic | Type | Description |
|-------|------|-------------|
| `/mouth/audio_wave` | `Float32MultiArray` | 128-point waveform from system audio |
| `/audio/level` | `Float32` | RMS level 0..1 |

## Parameters (rosparam)

All under namespace `/sound_mouth_sync/`:

```yaml
sound_mouth_sync:
  emotions:
    list: [neutral, happy, sad, angry, surprised, excited, sleepy, love,
           confused, scared, bored, calm, disgusted, tired]
    default_emotion: neutral
  display:
    default_emotion: neutral
    auto_mode: true                # auto-switch to oscillogram on sound
    silence_return_sec: 3.0        # seconds before returning to emotion
  audio_capture:
    source: pipewire_monitor       # pipewire_monitor | pulse_monitor | alsa
    pulse_source: ""               # auto-detect
    device: "plughw:2,0"           # ALSA device
    rate: 16000                    # sample rate
    chunk_size: 1024               # samples per chunk
    wave_width: 128                # oscillogram width
  hardware:
    i2c_port: 1
    i2c_address: 0x3D              # = 61 decimal
    width: 128
    height: 64
```

## Valid Emotions (built-in)

`neutral`, `happy`, `sad`, `angry`, `surprised`, `excited`, `sleepy`, `love`,
`confused`, `scared`, `bored`, `calm`, `disgusted`, `tired`

Custom emotions: place PNG in `resources/emotions/<name>.png`.

## Auto-mode Behaviour

1. Node starts in `emotion` mode showing `default_emotion`.
2. When `auto_mode=true` and `audio_capture_node` publishes data with RMS >= 0.02,
   display switches to `oscillogram`.
3. After `silence_return_sec` seconds of no audio data (or RMS < 0.02), display
   returns to `emotion` mode, showing the last set emotion.
4. Manual `/mouth/mode` messages override auto-mode state.

## Audio Capture Fallback Chain

```
pipewire_monitor (pw-record)
  → on 2+ failures with "Broken pipe" →
pulse_monitor (parec with auto-detected monitor source)
  → if parec/pactl unavailable →
error logged, retry every 5s
```

## Hardware

- Display: SSD1306 OLED, 128x64 pixels, monochrome, I2C bus 1, address 0x3D
- Audio: system sound card output (captured via loopback, not microphone by default)
- Platform: Raspberry Pi 5, Ubuntu, ROS Noetic

## Dependencies

- Python: `luma.oled`, `Pillow`, `PyYAML`, `rospy`, `rospkg`
- System: `pipewire` + `pipewire-pulse` (or `pulseaudio-utils`), `alsa-utils`
- ROS: `rospy`, `std_msgs`

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-03-19 | AI Agent | Initial creation. Two nodes: display_node + audio_capture_node. |
