"""
Generate alert WAV files using only numpy.
Run once before first bot launch: python generate_sounds.py

Outputs:
  dashboard/assets/alert_long.wav   — 3-tone ascending chime C4-E4-G4  (0.8 s)
  dashboard/assets/alert_short.wav  — 2-tone descending chime G4-C4    (0.6 s)
"""
import os
import struct
import wave

import numpy as np

SAMPLE_RATE = 44100
AMPLITUDE   = 0.35
FADE_MS     = 12   # ms fade-in/out per note to prevent clicks

# Equal-temperament note frequencies (Hz)
C4, E4, G4 = 261.63, 329.63, 392.00


def _note(freq: float, duration_s: float) -> np.ndarray:
    n       = int(SAMPLE_RATE * duration_s)
    t       = np.linspace(0, duration_s, n, endpoint=False)
    samples = AMPLITUDE * np.sin(2 * np.pi * freq * t)
    fade    = int(SAMPLE_RATE * FADE_MS / 1000)
    samples[:fade]  *= np.linspace(0, 1, fade)
    samples[-fade:] *= np.linspace(1, 0, fade)
    return samples


def _silence(ms: float) -> np.ndarray:
    return np.zeros(int(SAMPLE_RATE * ms / 1000))


def _save(path: str, data: np.ndarray) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pcm = (data * 32767).clip(-32768, 32767).astype(np.int16)
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())
    print(f"  wrote {path}  ({len(data) / SAMPLE_RATE:.2f} s)")


def main():
    root   = os.path.dirname(os.path.abspath(__file__))
    assets = os.path.join(root, "dashboard", "assets")

    # alert_long.wav — ascending C4 → E4 → G4
    long_data = np.concatenate([
        _note(C4, 0.22), _silence(30),
        _note(E4, 0.22), _silence(30),
        _note(G4, 0.36),
    ])
    _save(os.path.join(assets, "alert_long.wav"), long_data)

    # alert_short.wav — descending G4 → C4
    short_data = np.concatenate([
        _note(G4, 0.28), _silence(30),
        _note(C4, 0.32),
    ])
    _save(os.path.join(assets, "alert_short.wav"), short_data)

    print("Done. Sound files ready for audio_alert.py")


if __name__ == "__main__":
    main()
