"""Animated 'how to play' hand pictures for the attract screen: aim, then shoot.

Pure pygame vector drawing, so it works on any screen and needs no assets.
"""
from __future__ import annotations

import math

import pygame

from . import config as C
from .fx import add_glow, draw_text, scale_color
from .render import draw_hud_panel


def _smooth(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def _capsule(dst, a, b, w, fill, edge):
    pygame.draw.line(dst, edge, a, b, int(w + 5))
    pygame.draw.circle(dst, edge, (int(a[0]), int(a[1])), int(w / 2 + 2))
    pygame.draw.circle(dst, edge, (int(b[0]), int(b[1])), int(w / 2 + 2))
    pygame.draw.line(dst, fill, a, b, int(w))
    pygame.draw.circle(dst, fill, (int(a[0]), int(a[1])), int(w / 2))
    pygame.draw.circle(dst, fill, (int(b[0]), int(b[1])), int(w / 2))


def draw_hand(dst, cx, cy, s, curl, col, pinch: bool = False):
    """A stylised hand. s ~ palm width in px. curl 0 = open, 1 = fist (or fully pinched)."""
    edge = col
    fill = scale_color(col, 0.22)
    fw = 0.2 * s
    fingers = ((-0.34, 0.62), (-0.115, 0.80), (0.115, 0.86), (0.34, 0.70))
    palm = pygame.Rect(int(cx - 0.46 * s), int(cy - 0.12 * s), int(0.92 * s), int(0.82 * s))
    tips = []
    for i, (dx, L) in enumerate(fingers):
        c = curl
        if pinch:
            c = curl * 0.55 if i == 1 else 0.0              # only the index curls toward the thumb
        base = (cx + dx * s, cy - 0.08 * s)
        length = L * s * (1.0 - 1.15 * c)
        tip = (base[0] + (0.06 * s * c if dx < 0 else -0.06 * s * c), base[1] - length)
        tips.append((base, tip))
    # thumb
    t_base = (cx - 0.42 * s, cy + 0.40 * s)
    t_open = (cx - 0.92 * s, cy + 0.02 * s)
    t_closed = (cx - 0.12 * s, cy + 0.16 * s)
    if pinch:
        idx_tip = tips[1][1]
        t_closed = (idx_tip[0] - 0.04 * s, idx_tip[1] + 0.05 * s)
    k = curl
    t_tip = (t_open[0] + (t_closed[0] - t_open[0]) * k, t_open[1] + (t_closed[1] - t_open[1]) * k)

    add_glow(dst, cx, cy, col, s * 1.3, 0.22)
    fist = curl > 0.5 and not pinch
    if not fist:
        for base, tip in tips:
            _capsule(dst, base, tip, fw, fill, edge)
    pygame.draw.rect(dst, edge, palm.inflate(5, 5), border_radius=int(0.2 * s))
    pygame.draw.rect(dst, fill, palm, border_radius=int(0.2 * s))
    if fist:
        for base, tip in tips:
            _capsule(dst, base, tip, fw, fill, edge)
    _capsule(dst, t_base, t_tip, fw * 1.05, fill, edge)
    if pinch and curl > 0.8:                                   # spark where the fingers meet
        pygame.draw.circle(dst, C.WHITE, (int(t_tip[0]), int(t_tip[1])), int(0.1 * s))


def _mini_target(dst, x, y, r, col, alive_frac=1.0):
    pygame.draw.rect(dst, scale_color(col, 0.2 * alive_frac), (x - r, y - r, 2 * r, 2 * r))
    pygame.draw.rect(dst, scale_color(col, alive_frac), (x - r, y - r, 2 * r, 2 * r), 3)
    pygame.draw.rect(dst, scale_color(col, 0.7 * alive_frac), (x - r // 2, y - r // 2, r, r), 2)


def _mini_reticle(dst, x, y, col):
    pygame.draw.circle(dst, col, (int(x), int(y)), 17, 2)
    pygame.draw.line(dst, col, (x - 24, y), (x - 8, y), 2)
    pygame.draw.line(dst, col, (x + 8, y), (x + 24, y), 2)
    pygame.draw.line(dst, col, (x, y - 24), (x, y - 8), 2)
    pygame.draw.line(dst, col, (x, y + 8), (x, y + 24), 2)


def _panel(dst, left, top, w, h, color, title):
    draw_hud_panel(dst, (left, top, w, h), color, alpha=185)
    draw_text(dst, title, 26, color, (left + 22, top + 26), "midleft")


def draw_howto_panels(dst, now: float, autofire: bool, gesture: str, left: int = 24, top: int = 246):
    """Two looping mini-lessons: 1) AIM with an open hand  2) SHOOT (or hold steady in easy mode)."""
    w, h = 376, 196
    pinch = gesture == "pinch"
    # ---------------------------------------------------------------- 1  AIM
    _panel(dst, left, top, w, h, C.CYAN, "1   AIM")
    sway = math.sin(now * 1.6)
    hx, hy = left + 104 + sway * 16, top + 118
    draw_hand(dst, hx, hy, 70, 0.0, C.CYAN)
    sx, sy, sw, sh = left + 214, top + 58, 142, 92
    pygame.draw.rect(dst, (6, 10, 26), (sx, sy, sw, sh), border_radius=8)
    pygame.draw.rect(dst, scale_color(C.CYAN, 0.6), (sx, sy, sw, sh), 2, border_radius=8)
    _mini_target(dst, sx + sw // 2, sy + sh // 2, 12, C.MAGENTA)
    _mini_reticle(dst, sx + sw / 2 + sway * 46, sy + sh / 2 + math.cos(now * 1.6) * 8, C.CYAN)
    draw_text(dst, "MOVE YOUR OPEN HAND", 20, C.WHITE, (left + w // 2, top + h - 20), glow=False)

    # ---------------------------------------------------------------- 2  SHOOT
    top2 = top + h + 14
    title = "2   HOLD STEADY" if autofire else "2   SHOOT"
    _panel(dst, left, top2, w, h, C.ORANGE, title)
    T = 3.8
    t = now % T
    if autofire:
        curl = 0.0
        prog = _smooth((t - 0.3) / 1.6)
        fired = t > 1.9
    else:
        curl = _smooth((t - 1.2) / 0.3) - _smooth((t - 2.3) / 0.4)
        fired = t > 1.5
    draw_hand(dst, left + 104, top2 + 118, 70, curl, C.ORANGE, pinch=pinch and not autofire)
    sx, sy = left + 214, top2 + 58
    pygame.draw.rect(dst, (6, 10, 26), (sx, sy, sw, sh), border_radius=8)
    pygame.draw.rect(dst, scale_color(C.ORANGE, 0.6), (sx, sy, sw, sh), 2, border_radius=8)
    tx, ty = sx + sw // 2, sy + sh // 2
    since = t - (1.9 if autofire else 1.5)
    if not fired or t > 3.2:
        _mini_target(dst, tx, ty, 12, C.MAGENTA, 1.0 if t < 3.2 else _smooth((t - 3.2) / 0.5))
    elif 0 <= since < 0.7:
        r = int(8 + 70 * since)
        pygame.draw.circle(dst, scale_color(C.ORANGE, 1 - since / 0.7), (tx, ty), r, 3)
        for k in range(8):
            a = k * math.pi / 4
            pygame.draw.line(dst, scale_color(C.GOLD, 1 - since / 0.7),
                             (tx + math.cos(a) * r * 0.5, ty + math.sin(a) * r * 0.5),
                             (tx + math.cos(a) * r * 1.0, ty + math.sin(a) * r * 1.0), 3)
    _mini_reticle(dst, tx, ty, C.GREEN if autofire else C.CYAN)
    if autofire and not fired:
        rect = pygame.Rect(0, 0, 54, 54)
        rect.center = (tx, ty)
        pygame.draw.arc(dst, C.GREEN, rect, math.pi / 2, math.pi / 2 + math.tau * prog, 5)
    if autofire:
        cap = "KEEP IT ON THE TARGET"
    else:
        cap = "PINCH YOUR FINGERS" if pinch else "CLOSE YOUR FIST"
    draw_text(dst, cap, 20, C.WHITE, (left + w // 2, top2 + h - 20), glow=False)
