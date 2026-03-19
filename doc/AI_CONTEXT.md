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
| `scripts/display_node.py` | Node 1: OLED display | Subscribes `/mouth/mode`, `/mouth/emotion`, `/mouth/audio_wave`. Publishes `/mouth/current_mode`, `/mouth/current_emotion`. Draws on OLED 0x3D via luma.oled. Supports `rotate` param for physically flipped display. |
| `scripts/audio_capture_node.py` | Node 2: audio capture | Captures system audio (PipeWire/Pulse/ALSA). Auto-detects USB sound card. Publishes `/mouth/audio_wave` (Float32MultiArray, 128 pts) and `/audio/level` (Float32). Starts full PipeWire stack (daemon + session manager + pulse). |
| `scripts/sms_config.py` | Config module | Loads `config/sound_mouth_sync.yaml` + rosparam overrides. Used by both nodes. |
| `scripts/usb_audio_reset.sh` | USB audio reset | Resets USB audio device via sysfs `authorized` toggle. Run with sudo after reboot if card not detected. |
| `scripts/audio_diag.sh` | Audio diagnostics | Checks ALSA devices, PipeWire/PulseAudio status, environment, optional capture test. |
| `config/sound_mouth_sync.yaml` | Parameters | emotions list, display settings, audio capture settings, hardware (I2C, rotate). |
| `launch/sound_mouth_sync.launch` | Launch file | Loads config, starts both nodes with args. |
| `resources/emotions/` | Custom PNGs | 128x64 1-bit. Loaded at node startup. Name = emotion name. |
| `README.md` | User docs | Examples, installation, parameters, troubleshooting. |
| `doc/ARCHITECTURE.md` | Architecture | Mermaid diagrams, node descriptions, data flow. |
| `doc/AI_CONTEXT.md` | This file | AI agent rules, full context. |
| `ROADMAP.md` | Roadmap | Future plans: animated emotions, advanced visualisation, emotion engine. |

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
    device: ""                     # ALSA device (auto-detect USB card if empty)
    rate: 16000                    # sample rate
    chunk_size: 1024               # samples per chunk
    wave_width: 128                # oscillogram width
  hardware:
    i2c_port: 1
    i2c_address: 0x3D              # = 61 decimal
    width: 128
    height: 64
    rotate: 2                      # 0=normal, 2=180° (display mounted upside-down)
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

## Audio Capture Startup

On startup, `audio_capture_node` performs:
1. Auto-detects USB sound card via `/proc/asound/cards`
2. Logs all ALSA devices and PulseAudio sinks/sources for diagnostics
3. Ensures full PipeWire stack is running: `pipewire` daemon + session manager (`pipewire-media-session` or `wireplumber`) + `pipewire-pulse`
4. Finds the correct monitor source (prefers USB audio card)
5. Warns after 200 consecutive silent chunks if capture may be misconfigured

## Audio Capture Fallback Chain

```
pipewire_monitor (pw-record)
  → if pipewire daemon not running or 2+ failures →
pulse_monitor (parec with auto-detected monitor source)
  → if parec/pactl unavailable →
error logged, retry every 5s
```

## Hardware

- Display: SSD1306 OLED, 128x64 pixels, monochrome, I2C bus 1, address 0x3D, mounted upside-down (rotate=2)
- Audio: GeneralPlus USB Audio Device (card 2), single speaker, USB path 1-1.4
- Platform: Raspberry Pi 5, Ubuntu 20.04, ROS Noetic

## Dependencies

- Python: `luma.oled`, `Pillow`, `PyYAML`, `rospy`, `rospkg`
- System: `pipewire` + `pipewire-pulse` + `pipewire-media-session` (or `wireplumber`), `alsa-utils`
- ROS: `rospy`, `std_msgs`

## Known Issues

- USB sound card may not initialize after reboot. Fix: `sudo scripts/usb_audio_reset.sh`
- PipeWire 0.2.x config at `/etc/pipewire/pipewire.conf` is incompatible with PipeWire 1.0.7. The node now ignores `PIPEWIRE_CONFIG_FILE` env var and uses the system default config.
- Emotions are static (no animation). See `ROADMAP.md` for planned animated emotions.

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-03-19 | AI Agent | Initial creation. Two nodes: display_node + audio_capture_node. |
| 2026-03-19 | AI Agent | Fix OLED inversion (rotate=2). Rewrite audio capture: proper PipeWire session startup, USB card auto-detect, startup diagnostics, silence detection warning. Add usb_audio_reset.sh, audio_diag.sh, ROADMAP.md. |
