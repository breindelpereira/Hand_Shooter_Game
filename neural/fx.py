"""Visual effects: additive glow, neon text, particles, the tunnel background."""
from __future__ import annotations

import math
import random

import numpy as np
import pygame

from . import config as C

# ---------------------------------------------------------------- glow
_glow_cache: dict = {}


def glow_sprite(color, radius: int, power: float = 2.2) -> pygame.Surface:
    radius = int(max(3, min(radius, 360)))
    if radius > 24:
        radius = (radius + 3) // 4 * 4
    key = (color, radius, power)
    spr = _glow_cache.get(key)
    if spr is None:
        size = radius * 2
        ys, xs = np.mgrid[0:size, 0:size]
        d = np.hypot(xs - radius + 0.5, ys - radius + 0.5) / radius
        a = np.clip(1.0 - d, 0.0, 1.0) ** power
        arr = np.empty((size, size, 3), np.uint8)
        for i in range(3):
            arr[..., i] = (a * color[i]).astype(np.uint8)
        spr = pygame.surfarray.make_surface(arr.swapaxes(0, 1)).convert()
        if len(_glow_cache) > 900:
            _glow_cache.clear()
        _glow_cache[key] = spr
    return spr


def add_glow(dst: pygame.Surface, x: float, y: float, color, radius: float,
             intensity: float = 1.0, power: float = 2.2):
    if intensity <= 0.03 or radius < 2:
        return
    q = round(min(1.0, intensity) * 8) / 8
    if q <= 0:
        return
    c = (int(color[0] * q), int(color[1] * q), int(color[2] * q))
    spr = glow_sprite(c, radius, power)
    r = spr.get_width() // 2
    dst.blit(spr, (int(x) - r, int(y) - r), special_flags=pygame.BLEND_RGB_ADD)


def scale_color(color, k: float):
    k = max(0.0, min(1.0, k))
    return (int(color[0] * k), int(color[1] * k), int(color[2] * k))


def mix(a, b, t):
    return (int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t))


# ---------------------------------------------------------------- text
_font_cache: dict = {}
_text_cache: dict = {}
_plate_cache: dict = {}


def font(size: int, bold: bool = True) -> pygame.font.Font:
    key = (size, bold)
    f = _font_cache.get(key)
    if f is None:
        f = pygame.font.SysFont("bahnschrift,segoeuisemibold,segoeui,arial", size, bold=bold)
        _font_cache[key] = f
    return f


def _neon_surface(s: str, size: int, color, glow: bool):
    """Text rendered onto black so it can be added to the frame with BLEND_RGB_ADD
    (an alpha glyph surface would add its whole rectangle)."""
    base = font(size).render(s, True, color)
    pad = max(8, size // 2) if glow else 0
    w, h = base.get_width() + pad * 2, base.get_height() + pad * 2
    text = pygame.Surface((w, h)).convert()
    text.fill((0, 0, 0))
    text.blit(base, (pad, pad))
    if not glow:
        return text, pad
    small = pygame.transform.smoothscale(text, (max(1, w // 4), max(1, h // 4)))
    blur = pygame.transform.smoothscale(small, (w, h))
    out = text.copy()
    out.blit(blur, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
    if size >= 56:
        out.blit(blur, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
    return out, pad


def draw_text(dst: pygame.Surface, s: str, size: int, color, pos, anchor: str = "center",
              glow: bool = True, fade: float = 1.0, plate: bool = True):
    """Neon text, drawn additively (black background pixels vanish).
    Small plain text gets a minimum size and a dark plate behind it so it stays readable
    over bright scenery."""
    if fade <= 0.03 or not s:
        return None
    if not glow and size < 18:
        size = 18
    col = scale_color(color, round(fade * 8) / 8)
    key = (s, size, col, glow)
    entry = _text_cache.get(key)
    if entry is None:
        if len(_text_cache) > 500:
            _text_cache.clear()
        entry = _neon_surface(s, size, col, glow)
        _text_cache[key] = entry
    surf, pad = entry
    r = surf.get_rect()
    setattr(r, anchor, (int(pos[0]), int(pos[1])))
    # the glow padding must not shift where the glyphs sit
    if "left" in anchor:
        r.x -= pad
    elif "right" in anchor:
        r.x += pad
    if "top" in anchor:
        r.y -= pad
    elif "bottom" in anchor:
        r.y += pad
    if plate and not glow and size <= 34 and fade > 0.5:
        pk = (r.w, r.h)
        plate = _plate_cache.get(pk)
        if plate is None:
            if len(_plate_cache) > 300:
                _plate_cache.clear()
            plate = pygame.Surface((r.w + 20, r.h + 8), pygame.SRCALPHA)
            pygame.draw.rect(plate, (3, 5, 18, 165), plate.get_rect(), border_radius=8)
            _plate_cache[pk] = plate
        dst.blit(plate, (r.x - 10, r.y - 4))
    dst.blit(surf, r, special_flags=pygame.BLEND_RGB_ADD)
    return r


# ---------------------------------------------------------------- particles
class _P:
    __slots__ = ("x", "y", "vx", "vy", "life", "max", "color", "size", "drag", "grav", "chip")


class FX:
    """Particles, shockwave rings, floating text, tracers, screen shake and flashes."""

    MAX_PARTS = 700

    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.parts: list[_P] = []
        self.rings: list[list] = []     # x,y,color,r0,r1,life,max,width
        self.texts: list[list] = []     # x,y,text,color,size,life,max,rise
        self.tracers: list[list] = []   # x0,y0,x1,y1,life,color
        self.shake = 0.0
        self.flash = 0.0                # red damage flash
        self.flash_white = 0.0
        self.callout = None             # [text, color, life, max]

    # --- emitters
    def burst(self, x, y, color, n=24, speed=(80, 460), life=(0.35, 0.9), size=(2, 5),
              grav=0.0, drag=1.6, chips=0.0):
        rng = self.rng
        for _ in range(n):
            if len(self.parts) >= self.MAX_PARTS:
                break
            p = _P()
            a = rng.uniform(0, math.tau)
            s = rng.uniform(*speed)
            p.x, p.y = x, y
            p.vx, p.vy = math.cos(a) * s, math.sin(a) * s
            p.max = p.life = rng.uniform(*life)
            p.color = color
            p.size = rng.uniform(*size)
            p.drag = drag
            p.grav = grav
            p.chip = rng.random() < chips
            self.parts.append(p)

    def ring(self, x, y, color, r1, life=0.5, width=3, r0=4):
        self.rings.append([x, y, color, r0, r1, life, life, width])

    def popup(self, x, y, text, color, size=28, life=0.9, rise=60):
        self.texts.append([x, y, text, color, size, life, life, rise])

    def tracer(self, x0, y0, x1, y1, color=C.CYAN, life=0.10):
        self.tracers.append([x0, y0, x1, y1, life, color])

    def big_callout(self, text, color=C.GOLD, life=1.1):
        self.callout = [text, color, life, life]

    def add_shake(self, amount):
        self.shake = min(26.0, self.shake + amount)

    def explosion(self, x, y, color, size=1.0, big=False):
        n = int(26 * size) + (30 if big else 0)
        self.burst(x, y, color, n=n, speed=(90, 520 * (1.3 if big else 1)), size=(2, 5))
        # chunky cube shards that tumble and fall a little
        self.burst(x, y, color, n=int(10 * size) + (14 if big else 0), speed=(60, 380),
                   life=(0.5, 1.1), size=(4, 9), grav=260.0, drag=1.1, chips=1.0)
        self.burst(x, y, C.WHITE, n=max(6, n // 4), speed=(40, 260), life=(0.15, 0.4), size=(1, 3))
        self.ring(x, y, color, r1=70 * size + (60 if big else 0), life=0.45, width=4)
        if big:
            self.ring(x, y, C.WHITE, r1=170 * size, life=0.6, width=2)

    # --- simulation / drawing
    def update(self, dt):
        for p in self.parts:
            p.life -= dt
            k = max(0.0, 1.0 - p.drag * dt)
            p.vx *= k
            p.vy = p.vy * k + p.grav * dt
            p.x += p.vx * dt
            p.y += p.vy * dt
        self.parts = [p for p in self.parts if p.life > 0]
        for r in self.rings:
            r[5] -= dt
        self.rings = [r for r in self.rings if r[5] > 0]
        for t in self.texts:
            t[5] -= dt
        self.texts = [t for t in self.texts if t[5] > 0]
        for t in self.tracers:
            t[4] -= dt
        self.tracers = [t for t in self.tracers if t[4] > 0]
        self.shake = max(0.0, self.shake - 70.0 * dt)
        self.flash = max(0.0, self.flash - 1.8 * dt)
        self.flash_white = max(0.0, self.flash_white - 4.0 * dt)
        if self.callout:
            self.callout[2] -= dt
            if self.callout[2] <= 0:
                self.callout = None

    def shake_offset(self):
        if self.shake <= 0.5:
            return 0, 0
        s = self.shake
        return (int(self.rng.uniform(-s, s)), int(self.rng.uniform(-s, s)))

    def draw(self, dst):
        for t in self.tracers:
            f = t[4] / 0.10
            col = t[5]
            pygame.draw.line(dst, scale_color(col, f), (t[0], t[1]), (t[2], t[3]), 3)
            pygame.draw.line(dst, scale_color(C.WHITE, f), (t[0], t[1]), (t[2], t[3]), 1)
        for p in self.parts:
            f = p.life / p.max
            col = scale_color(p.color, min(1.0, f * 1.4))
            x, y = int(p.x), int(p.y)
            if p.chip:
                s = max(2, int(p.size * (0.4 + 0.6 * f)))
                pygame.draw.rect(dst, scale_color(p.color, 0.25 * min(1.0, f * 2)), (x - s, y - s, s * 2, s * 2))
                pygame.draw.rect(dst, col, (x - s, y - s, s * 2, s * 2), 1)
                continue
            pygame.draw.line(dst, col, (x, y), (int(p.x - p.vx * 0.03), int(p.y - p.vy * 0.03)),
                             max(1, int(p.size * f)))
            if p.size > 3.2 and f > 0.35:
                add_glow(dst, x, y, p.color, p.size * 3.2, f * 0.7)
        for r in self.rings:
            f = 1.0 - r[5] / r[6]
            rad = int(r[3] + (r[4] - r[3]) * (1 - (1 - f) ** 2))
            pygame.draw.circle(dst, scale_color(r[2], 1.0 - f), (int(r[0]), int(r[1])), rad,
                               max(1, int(r[7] * (1 - f)) + 1))
        for t in self.texts:
            f = 1.0 - t[5] / t[6]
            draw_text(dst, t[2], t[4], t[3], (t[0], t[1] - t[7] * f), fade=min(1.0, (1 - f) * 2.2))
        if self.callout:
            txt, col, life, mx = self.callout
            f = 1.0 - life / mx
            sz = int(70 + 30 * max(0.0, 1 - f * 5))
            draw_text(dst, txt, sz, col, (C.CX, C.H * 0.26), fade=min(1.0, (1 - f) * 2.0))
