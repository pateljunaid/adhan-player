"""Loudness measurement and playback gains, using only what ships with macOS.

measure_lufs() decodes with afconvert and computes ITU-R BS.1770 integrated loudness (LUFS)
in pure Python, so the Mac that plays the adhans can measure new mp3s itself (no ffmpeg).
Audio is decoded at 8 kHz to keep this quick on an old Mac; K-weighting barely cares about
what is lost above 4 kHz for recitation.
"""
import array
import json
import math
import subprocess
import tempfile
import wave
from pathlib import Path

ANALYSIS_RATE = 8000
ABSOLUTE_GATE_LUFS = -70.0
RELATIVE_GATE_LU = -10.0


def _k_weighting(fs):
    """Coefficients of the two BS.1770 pre-filters (high shelf, then high-pass) at rate fs."""
    k = math.tan(math.pi * 1681.974450955533 / fs)
    q = 0.7071752369554196
    vh = 10 ** (3.999843853973347 / 20)
    vb = vh ** 0.4996667741545416
    a0 = 1 + k / q + k * k
    shelf = ((vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0,
             2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0)
    k = math.tan(math.pi * 38.13547087602444 / fs)
    q = 0.5003270373238773
    a0 = 1 + k / q + k * k
    highpass = (2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0)
    return shelf, highpass


def _segment_energies(samples, fs):
    """Mean-square K-weighted energy of each 100 ms segment of one channel."""
    (b0, b1, b2, a1, a2), (c1, c2) = _k_weighting(fs)
    segment = fs // 10
    z1 = z2 = d1 = d2 = 0.0
    energies = []
    for start in range(0, len(samples) - segment + 1, segment):
        total = 0.0
        for x in samples[start:start + segment]:
            y = b0 * x + z1
            z1 = b1 * x - a1 * y + z2
            z2 = b2 * x - a2 * y
            v = y + d1
            d1 = -2 * y - c1 * v + d2
            d2 = y - c2 * v
            total += v * v
        energies.append(total / segment / 32768 ** 2)
    return energies


def _loudness(energy):
    return -0.691 + 10 * math.log10(energy)


def measure_lufs(path):
    """Integrated loudness of an audio file in LUFS."""
    with tempfile.TemporaryDirectory() as tmp:
        wav_path = Path(tmp) / 'audio.wav'
        subprocess.run(['afconvert', '-f', 'WAVE', '-d', f'LEI16@{ANALYSIS_RATE}', str(path), str(wav_path)],
                       check=True)
        with wave.open(str(wav_path)) as wav:
            channels, fs = wav.getnchannels(), wav.getframerate()
            pcm = array.array('h')
            pcm.frombytes(wav.readframes(wav.getnframes()))

    per_channel = [_segment_energies(pcm[c::channels], fs) for c in range(channels)]
    # a 400 ms block is 4 segments (75% overlap); channel energies add
    blocks = [sum(sum(ch[i:i + 4]) / 4 for ch in per_channel)
              for i in range(len(per_channel[0]) - 3)]
    loud = [b for b in blocks if b > 0 and _loudness(b) > ABSOLUTE_GATE_LUFS]
    if not loud:
        raise ValueError(f'{path} is silent')
    relative_gate = _loudness(sum(loud) / len(loud)) + RELATIVE_GATE_LU
    gated = [b for b in loud if _loudness(b) > relative_gate]
    return _loudness(sum(gated) / len(gated))


ADHAN_DIRS = ('mp3/fajr/', 'mp3/non-fajr/')


def compute_gains(entries):
    """Playback gains (<= 1.0) that bring every file down to the quietest adhan's loudness.

    `entries` maps a path relative to the repo to {'lufs': ...}. Gains never exceed 1.0
    because boosting a file could clip it; set the overall level with the Mac's volume.
    """
    adhan_levels = [e['lufs'] for name, e in entries.items() if name.startswith(ADHAN_DIRS)]
    if not adhan_levels:
        return {}
    target = min(adhan_levels)
    return {name: round(min(1.0, 10 ** ((target - e['lufs']) / 20)), 4) for name, e in entries.items()}
