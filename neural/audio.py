"""Procedural audio: every sound effect and the music loop are synthesised with NumPy.

If no audio device is available the whole module degrades to silent no-ops.
"""
from __future__ import annotations

import math

import numpy as np
import pygame

SR = 44100
BPM = 126
_RNG = np.random.default_rng(12345)


# ---------------------------------------------------------------- synthesis
def _t(dur):
    return np.arange(int(SR * dur)) / SR


def _env(t, attack=0.004, tau=0.1):
    return np.minimum(1.0, t / max(attack, 1e-4)) * np.exp(-t / tau)


def _sweep(f0, f1, dur, wave="sine", tau=None, curve=3.0):
    t = _t(dur)
    f = f1 + (f0 - f1) * np.exp(-curve * t / dur)
    ph = np.cumsum(f) / SR * math.tau
    if wave == "sine":
        w = np.sin(ph)
    elif wave == "square":
        w = np.sign(np.sin(ph)) * 0.6
    else:  # saw
        w = 2.0 * ((ph / math.tau) % 1.0) - 1.0
    return w * _env(t, 0.003, tau or dur / 3)


def _tone(freq, dur, wave="sine", tau=None, attack=0.004):
    t = _t(dur)
    ph = t * freq * math.tau
    if wave == "sine":
        w = np.sin(ph)
    elif wave == "square":
        w = np.sign(np.sin(ph)) * 0.55
    else:
        w = 2.0 * ((t * freq) % 1.0) - 1.0
    return w * _env(t, attack, tau or dur / 3)


def _noise(dur, tau=None, lp=0):
    t = _t(dur)
    n = _RNG.uniform(-1, 1, t.size)
    if lp > 1:
        n = np.convolve(n, np.ones(lp) / lp, mode="same")
    return n * _env(t, 0.002, tau or dur / 3)


def _add(buf, start, snd):
    """Add snd into buf at start, wrapping the tail around so loops are seamless."""
    n = buf.size
    end = start + snd.size
    if end <= n:
        buf[start:end] += snd
    else:
        k = n - start
        buf[start:] += snd[:k]
        rest = min(end - n, n)
        buf[:rest] += snd[k:k + rest]


def _mix(*parts):
    n = max(p.size for p in parts)
    out = np.zeros(n)
    for p in parts:
        out[:p.size] += p
    return out


def _to_sound(a, vol=0.6, pan=0.0):
    peak = float(np.max(np.abs(a))) or 1.0
    a = a / peak * vol
    l = a * (1.0 - max(0.0, pan))
    r = a * (1.0 + min(0.0, pan))
    st = np.stack([l, r], axis=1)
    return pygame.sndarray.make_sound((st * 32000).astype(np.int16))


def _smooth(e0, e1, x):
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


class Audio:
    def __init__(self, enabled: bool = True):
        self.enabled = False
        self.muted = False
        self.sfx_gain = 1.0
        self.music_gain = 0.55
        self.sfx_muted = False           # attract mode silences SFX
        self.sounds: dict = {}
        self.hits: list = []
        self.stems: list = []
        self._stem_vol = [0.0] * 5
        self._stem_target = [0.0] * 5
        self._music_on = False
        if not enabled:
            return
        try:
            pygame.mixer.init(SR, -16, 2, 512)
            pygame.mixer.set_num_channels(24)
            pygame.mixer.set_reserved(5)
            self._build()
            self.enabled = True
        except Exception as exc:  # noqa: BLE001 - no audio device etc.
            print(f"[audio] disabled: {exc}")

    # ---- build
    def _build(self):
        S = self.sounds
        S["shoot"] = _to_sound(_mix(_sweep(1500, 160, 0.13, "saw", 0.05),
                                    _noise(0.03, 0.01) * 0.5), 0.40)
        for i in range(10):
            f = 440 * 2 ** (i / 6.0)
            self.hits.append(_to_sound(_mix(_tone(f, 0.16, "sine", 0.05),
                                            _tone(f * 2, 0.10, "square", 0.025) * 0.35,
                                            _noise(0.05, 0.015) * 0.4), 0.55))
        S["explode"] = _to_sound(_mix(_noise(0.5, 0.11, lp=6),
                                      _sweep(180, 40, 0.5, "sine", 0.15)), 0.75)
        S["big_explode"] = _to_sound(_mix(_noise(1.0, 0.25, lp=10),
                                          _sweep(140, 30, 1.0, "sine", 0.3) * 1.2), 0.9)
        S["mine"] = _to_sound(_mix(_sweep(110, 28, 0.7, "saw", 0.2),
                                   _noise(0.6, 0.15, lp=14)), 0.9)
        notes = [523.25, 659.25, 783.99, 1046.5]
        S["powerup"] = _to_sound(np.concatenate([_tone(f, 0.07, "square", 0.05) for f in notes]), 0.45)
        S["leak"] = _to_sound(_mix(_sweep(320, 60, 0.4, "saw", 0.18), _noise(0.3, 0.1, lp=3) * 0.3), 0.7)
        S["level_clear"] = _to_sound(np.concatenate(
            [_tone(f, 0.12, "square", 0.09) for f in (392, 523.25, 659.25, 783.99, 1046.5)]), 0.5)
        S["game_over"] = _to_sound(np.concatenate(
            [_tone(f, 0.28, "saw", 0.2) for f in (392, 329.6, 261.6, 196)]), 0.6)
        S["tick"] = _to_sound(_tone(880, 0.10, "sine", 0.05), 0.6)
        S["go"] = _to_sound(_mix(_tone(1318, 0.35, "square", 0.15), _tone(659, 0.35, "sine", 0.15)), 0.6)
        S["click"] = _to_sound(_tone(1200, 0.05, "sine", 0.02), 0.5)
        S["charge"] = _to_sound(_sweep(150, 1300, 0.55, "sine", 0.5, curve=3.0) * 0.8, 0.45)
        S["blast"] = _to_sound(_mix(_noise(0.8, 0.2, lp=5), _sweep(900, 50, 0.8, "saw", 0.25)), 0.85)
        S["shield"] = _to_sound(_mix(_tone(740, 0.12, "square", 0.04), _tone(1480, 0.12, "sine", 0.05)), 0.5)
        S["emp"] = _to_sound(_noise(0.9, 0.5, lp=2) * (0.6 + 0.4 * np.sin(_t(0.9) * 90)), 0.55)
        S["alarm"] = _to_sound(np.concatenate([_tone(660, 0.18, "square", 0.3),
                                               _tone(520, 0.18, "square", 0.3)] * 2), 0.5)
        S["select"] = _to_sound(np.concatenate([_tone(660, 0.06, "sine", 0.04),
                                                _tone(990, 0.14, "sine", 0.09)]), 0.5)
        S["hover"] = _to_sound(_tone(1500, 0.035, "sine", 0.014), 0.22)
        S["pop"] = _to_sound(_mix(_tone(300, 0.1, "sine", 0.03), _noise(0.06, 0.02, lp=4)), 0.4)
        self._build_music()

    def _build_music(self):
        beat = 60.0 / BPM
        step = beat / 4.0
        n = int(round(16 * beat * SR))
        sp = lambda i: int(round(i * step * SR))
        roots = [55.0, 43.65, 65.41, 49.0]                       # A F C G
        chords = [(220.0, 261.63, 329.63), (174.61, 220.0, 261.63),
                  (261.63, 329.63, 392.0), (196.0, 246.94, 293.66)]

        bass = np.zeros(n)
        arp = np.zeros(n)
        kick = np.zeros(n)
        hats = np.zeros(n)
        pad = np.zeros(n)
        k = _sweep(160, 42, 0.22, "sine", 0.09, curve=5.0)
        h = _noise(0.05, 0.012)
        for bar in range(4):
            r = roots[bar]
            ch = chords[bar]
            for s in range(16):
                i = bar * 16 + s
                a = sp(i)
                if s % 2 == 0:  # 8th-note bass pulse
                    f = r * (2 if s in (6, 14) else 1)
                    note = _tone(f, step * 1.8, "saw", step * 1.0)
                    note = np.convolve(note, np.ones(8) / 8, mode="same")
                    _add(bass, a, note)
                # arpeggio: 16th notes through the chord (+ octave)
                f = (ch[s % 3]) * (2 if (s // 3) % 2 else 1)
                _add(arp, a, _tone(f, step * 0.9, "square", step * 0.5) * 0.5)
                if s % 4 == 0:
                    _add(kick, a, k)
                if s % 2 == 1:
                    _add(hats, a, h)
            # pad: smooth sin^2 swell per bar
            bar_n = sp(bar * 16 + 16) - sp(bar * 16)
            tt = np.arange(bar_n) / bar_n
            swell = np.sin(math.pi * tt) ** 2
            chord = sum(np.sin(np.arange(bar_n) / SR * f * math.tau) for f in (c / 2 for c in ch))
            pad[sp(bar * 16):sp(bar * 16) + bar_n] += chord * swell * 0.4
        self.stems = []
        for arr, gain in ((pad, 0.9), (kick, 1.0), (hats, 0.55), (bass, 0.9), (arp, 0.45)):
            self.stems.append(_to_sound(arr, 0.5 * gain))
        self._stem_vol = [0.0] * 5

    # ---- playback
    def play(self, name: str, vol: float = 1.0, pan: float = 0.0):
        if not self.enabled or self.muted or self.sfx_muted:
            return
        s = self.sounds.get(name)
        if s is not None:
            s.set_volume(min(1.0, vol * self.sfx_gain))
            s.play()

    def play_ui(self, name: str, vol: float = 1.0):
        """Menu sounds: unlike play(), these are not silenced while the attract demo runs."""
        if not self.enabled or self.muted:
            return
        s = self.sounds.get(name)
        if s is not None:
            s.set_volume(min(1.0, vol * self.sfx_gain))
            s.play()

    def play_hit(self, combo: int):
        if not self.enabled or self.muted or self.sfx_muted or not self.hits:
            return
        s = self.hits[min(combo, len(self.hits) - 1)]
        s.set_volume(self.sfx_gain)
        s.play()

    def music_start(self):
        if not self.enabled or self._music_on:
            return
        for i, st in enumerate(self.stems):
            ch = pygame.mixer.Channel(i)
            ch.set_volume(0.0)
            ch.play(st, loops=-1)
        self._music_on = True

    def music_stop(self):
        if not self.enabled:
            return
        for i in range(5):
            pygame.mixer.Channel(i).stop()
        self._music_on = False

    def set_intensity(self, x: float):
        """0..1: more layers fade in as the run intensifies."""
        x = min(1.0, max(0.0, x))
        self._stem_target = [
            0.55,
            _smooth(0.10, 0.30, x),
            _smooth(0.28, 0.48, x),
            _smooth(0.18, 0.40, x),
            _smooth(0.50, 0.72, x),
        ]

    def update(self, dt: float):
        if not self.enabled or not self._music_on:
            return
        for i in range(5):
            tgt = 0.0 if self.muted else self._stem_target[i] * self.music_gain
            self._stem_vol[i] += (tgt - self._stem_vol[i]) * min(1.0, dt * 2.5)
            pygame.mixer.Channel(i).set_volume(max(0.0, min(1.0, self._stem_vol[i])))

    def toggle_mute(self):
        self.muted = not self.muted
        return self.muted
