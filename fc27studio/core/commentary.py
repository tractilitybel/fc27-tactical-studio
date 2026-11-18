"""Voice commentary: WAV files placed on the timeline, mixed over the game
sound with automatic "ducking" (game sound dips while you talk)."""
from __future__ import annotations

import os
import wave
from typing import Optional

import numpy as np

from .audio import RATE
from .model import Commentary, Project

DUCK_LEVEL = 0.3        # game volume while commentary plays
DUCK_RAMP = 0.12        # seconds to fade the game down/up


def write_wav(path: str, mono: np.ndarray, rate: int = RATE) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    pcm = (np.clip(mono, -1, 1) * 32767).astype("<i2")
    tmp = path + ".part"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    os.replace(tmp, path)


def read_wav(path: str) -> np.ndarray:
    """Mono float32 at RATE (resampled linearly if needed)."""
    with wave.open(path, "rb") as w:
        ch, sw, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 2:
        data = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    elif sw == 4:
        data = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648.0
    else:
        data = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128) / 128.0
    data = data.reshape(-1, ch).mean(axis=1)
    if rate != RATE and len(data):
        x = np.arange(int(len(data) * RATE / rate)) * rate / RATE
        data = np.interp(x, np.arange(len(data)), data).astype(np.float32)
    return data


def resolve(clip: Commentary, project_dir: Optional[str]) -> str:
    if os.path.isabs(clip.file) or not project_dir:
        return clip.file
    return os.path.normpath(os.path.join(project_dir, clip.file))


class CommentaryMixer:
    """Callable(media_t0, game_block) -> mixed block. Thread-safe enough for
    read-only use from the audio threads (clips are loaded on demand)."""

    def __init__(self, project: Project, project_dir: Optional[str], fps: float):
        self.project = project
        self.dir = project_dir
        self.fps = fps or 30.0
        self._cache: dict[str, np.ndarray] = {}

    def _samples(self, clip: Commentary) -> np.ndarray:
        key = f"{clip.id}:{clip.file}"
        s = self._cache.get(key)
        if s is None:
            try:
                s = read_wav(resolve(clip, self.dir))
            except Exception:
                s = np.zeros(0, np.float32)
            self._cache[key] = s
        return s

    def active_clips(self, t0: float, t1: float) -> list[Commentary]:
        out = []
        for c in self.project.commentary:
            s = c.frame / self.fps
            if s < t1 and s + c.seconds > t0:
                out.append(c)
        return out

    def __call__(self, t0: float, game: np.ndarray) -> np.ndarray:
        n = len(game)
        if n == 0 or not self.project.commentary:
            return game
        t1 = t0 + n / RATE
        clips = self.active_clips(t0 - DUCK_RAMP, t1 + DUCK_RAMP)
        if not clips:
            return game
        voice = np.zeros(n, np.float32)
        presence = np.zeros(n, np.float32)
        for c in clips:
            data = self._samples(c)
            start = int(round((c.frame / self.fps - t0) * RATE))   # clip start relative to block
            a, b = max(0, start), min(n, start + len(data))
            if b > a:
                voice[a:b] += data[a - start:b - start] * c.gain
            # presence with ramps for smooth ducking
            ramp = int(DUCK_RAMP * RATE)
            pa, pb = start - ramp, start + len(data) + ramp
            idx = np.arange(n)
            env = np.clip(np.minimum((idx - pa) / max(1, ramp), (pb - idx) / max(1, ramp)), 0, 1)
            presence = np.maximum(presence, env.astype(np.float32))
        out = game.copy()
        if self.project.duck_game_audio:
            out *= (1.0 - (1.0 - DUCK_LEVEL) * presence)[:, None]
        out += voice[:, None]
        return out
