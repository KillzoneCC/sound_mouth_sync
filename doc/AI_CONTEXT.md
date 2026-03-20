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
| `scripts/display_node.py` | Node 1: OLED display | Subscribes `/mouth/mode`, `/mouth/emotion`, `/mouth/audio_wave`, `/robot/posture`, `/robot/is_moving`. Publishes `/mouth/current_mode`, `/mouth/current_emotion`. Priority: fallen → `fall_emotion`; idle timeout → `idle_sleep_emotion`; else user emotion + oscillogram. `sleepy` without `sleepy.png`: animated cigarette + smoke. Fall `angry` without `angry.png`: vector mouth + scratch. |
| `scripts/audio_capture_node.py` | Node 2: audio capture | Starts native PulseAudio, creates ALSA sink for USB card, enables TCP:4713 for host access. Captures via `parec` from `usb_output.monitor`. Publishes `/mouth/audio_wave` (Float32MultiArray, 128 pts) and `/audio/level` (Float32). |
| `scripts/sms_config.py` | Config module | Loads `config/sound_mouth_sync.yaml` + rosparam overrides. Used by both nodes. |
| `scripts/usb_audio_reset.sh` | USB audio reset | Resets USB audio device via sysfs `authorized` toggle. Run with sudo after reboot if card not detected. |
| `scripts/audio_diag.sh` | Audio diagnostics | Checks ALSA devices, PulseAudio status, environment, optional capture test. |
| `scripts/setup_host_audio.sh` | Host audio redirect | Run on Raspberry Pi host to route audio to Docker PulseAudio (sets PULSE_SERVER). |
| `config/sound_mouth_sync.yaml` | Parameters | emotions list, display settings, audio capture settings, hardware (I2C, rotate). |
| `launch/sound_mouth_sync.launch` | Launch file | Loads config, starts both nodes with args. |
| `resources/emotions/` | Custom PNGs | 128x64 1-bit. Loaded at node startup. Name = emotion name. |
| `README.md` | User docs | Examples, installation, parameters, troubleshooting. |
| `doc/ARCHITECTURE.md` | Architecture | Mermaid diagrams, node descriptions, data flow. |
| `doc/AI_CONTEXT.md` | This file | AI agent rules, full context. |
| `doc/AUDIO_PLAYBACK.md` | Audio playback guide | Developer guide: how to play sound so oscillogram works. Docker + host. |
| `ROADMAP.md` | Roadmap | Future plans: animated emotions, advanced visualisation, emotion engine. |

## Topics

### Subscribed by display_node

| Topic | Type | Values | Source |
|-------|------|--------|--------|
| `/mouth/mode` | `String` | `"emotion"`, `"oscillogram"` | Any external node |
| `/mouth/emotion` | `String` | Any from VALID_EMOTIONS or custom PNG name | Any external node |
| `/mouth/audio_wave` | `Float32MultiArray` | 128 floats in [-1, 1] | audio_capture_node |
| `/robot/posture` | `String` | `stand`, `fall_forward`, … | `joystick_control` (ainex_peripherals) |
| `/robot/is_moving` | `Bool` | gait moving | `joystick_control` (ainex_peripherals) |

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
    idle_sleep_enabled: true
    idle_sleep_sec: 60.0           # no speech + no walking → idle_sleep_emotion
    idle_sleep_emotion: sleepy
    fall_emotion: angry            # while posture is fall_*
    posture_topic: /robot/posture
    movement_topic: /robot/is_moving
    idle_require_movement_signal: true
  audio_capture:
    rate: 48000                    # sample rate Hz (must match USB card native rate)
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

## Display priority (emotion overlays)

1. **Fallen** (`/robot/posture` in `fall_*`): force emotion mode, show `fall_emotion` (default `angry`); ignore oscillogram until `stand`. Custom `angry.*` in `resources/emotions/` overrides the built-in angry bitmap.
2. **Normal**: oscillogram on sound (auto_mode), user emotion from `/mouth/emotion`.
3. **Idle sleep**: if enabled, not fallen, in emotion mode, and inactive for `idle_sleep_sec` — show `idle_sleep_emotion` (default `sleepy`). Custom `sleepy.*` overrides animated sleepy. Activity = audible RMS, oscillogram active, `/robot/is_moving` true, or `/mouth/emotion` received.

If `idle_require_movement_signal=true`, idle sleep is disabled until at least one `/robot/is_moving` message is received (avoids sleep without `joystick_control`).

## Audio Capture Startup

On startup, `audio_capture_node` performs:
1. Starts native PulseAudio daemon (kills pipewire-pulse if present)
2. Auto-detects USB sound card via `/proc/asound/cards`
3. Loads `module-alsa-sink` for the USB card → creates `usb_output` sink
4. Sets `usb_output` as default sink
5. Loads `module-native-protocol-tcp` (port 4713, auth-anonymous) for host access
6. Starts `parec` capturing from `usb_output.monitor`
7. Warns after 200 consecutive silent chunks if capture may be misconfigured

## Audio Architecture

PulseAudio inside Docker is the sole audio server owning the USB card. All audio
(from Docker nodes and from the Raspberry Pi host via TCP:4713) flows through it.
The monitor source captures everything for the oscillogram.

```
Docker:  TTS/paplay → PulseAudio → usb_output (ALSA) → USB speaker
                          ↓ .monitor
                   audio_capture_node → /mouth/audio_wave → display_node → OLED

Host:    VLC/aplay → PULSE_SERVER=tcp:127.0.0.1:4713 → (same PulseAudio above)
```

## Hardware

- Display: SSD1306 OLED, 128x64 pixels, monochrome, I2C bus 1, address 0x3D, mounted upside-down (rotate=2)
- Audio: GeneralPlus USB Audio Device (card 2), single speaker, USB path 1-1.4
- Platform: Raspberry Pi 5, Ubuntu 20.04, ROS Noetic

## Dependencies

- Python: `luma.oled`, `Pillow`, `numpy`, `PyYAML`, `rospy`, `rospkg`
- System (Docker): `pulseaudio`, `pulseaudio-utils`, `alsa-utils`
- System (Host): `pulseaudio-utils` (for `pactl`, `paplay`)
- ROS: `rospy`, `std_msgs`

## Known Issues

- USB sound card may not initialize after reboot. Fix: `sudo scripts/usb_audio_reset.sh`
- Host applications (VLC, aplay) must set `PULSE_SERVER=tcp:127.0.0.1:4713` to route audio through Docker's PulseAudio. Use `scripts/setup_host_audio.sh` for convenience.
- The USB card has no functional hardware loopback — PulseAudio monitor is the only way to capture playback.
- Most emotions are static; `sleepy` (idle, no custom asset) is animated (cigarette + smoke). See `ROADMAP.md` for further animation plans.

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-03-19 | AI Agent | Initial creation. Two nodes: display_node + audio_capture_node. |
| 2026-03-19 | AI Agent | Fix OLED inversion (rotate=2). Rewrite audio capture: proper PipeWire session startup, USB card auto-detect, startup diagnostics, silence detection warning. Add usb_audio_reset.sh, audio_diag.sh, ROADMAP.md. |
| 2026-03-19 | AI Agent | Rewrite audio capture to native PulseAudio (no PipeWire). Add TCP:4713 for host access, setup_host_audio.sh, AUDIO_PLAYBACK.md developer guide. Update all docs. |
| 2026-03-20 | AI Agent | Idle sleep + fall face: `joystick_control` publishes `/robot/posture`, `/robot/is_moving`; `display_node` subscribes; YAML + README + docs. |
| 2026-03-20 | AI Agent | Animated sleepy (cigarette + smoke) when no `sleepy.*`; fall angry vector mouth + scratch when no `angry.*`. |
