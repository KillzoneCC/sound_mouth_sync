# -*- coding: utf-8 -*-
"""Pure-Python audio gates for display_node (unit-testable without ROS)."""

# Keep in sync with display_node thresholds.
SILENCE_RMS_THRESHOLD = 0.015
SILENCE_PEAK_THRESHOLD = 0.008
AUDIO_LEVEL_THRESHOLD = 0.002


def waveform_is_audible(values):
    """True if 128-point wave buffer likely contains sound (not silence)."""
    if not values:
        return False
    n = len(values)
    if n <= 0:
        return False
    total = sum(v * v for v in values)
    rms = (total / n) ** 0.5
    peak = max(abs(v) for v in values)
    rng = max(values) - min(values)
    return (
        rms >= SILENCE_RMS_THRESHOLD
        or peak >= SILENCE_PEAK_THRESHOLD
        or rng >= (SILENCE_PEAK_THRESHOLD * 2.0)
    )


def audio_level_is_audible(level):
    """True if /audio/level (0..1 RMS vs full scale) indicates sound."""
    try:
        return abs(float(level)) >= AUDIO_LEVEL_THRESHOLD
    except (TypeError, ValueError):
        return False
