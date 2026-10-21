"""Drawing helpers for targets, the reticle and HUD primitives (neon vector style)."""
from __future__ import annotations

import math

import pygame

from . import config as C
from .fx import add_glow, mix, scale_color

# ---------------------------------------------------------------- 3D cube
_CV = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
       (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
_CF = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (3, 2, 6, 7), (0, 3, 7, 4), (1, 2, 6, 5)]


def draw_cube(dst, cx, cy, r, ang, color, w=2, scale=(1.0, 1.0, 1.0), fill=True, bright=1.0):
    """A solid, shaded, neon-edged cube whose circumscribed radius is ~r pixels.
    Faces are depth-sorted (painter's algorithm), so it is a proper convex solid."""
    ax, ay, az = ang
    ca, sa = math.cos(ax), math.sin(ax)
    cb, sb = math.cos(ay), math.sin(ay)
    cc, sc = math.cos(az), math.sin(az)
    k = r * 0.57
    pts = []
    for vx, vy, vz in _CV:
        x, y, z = vx * scale[0], vy * scale[1], vz * scale[2]
        y, z = y * ca - z * sa, y * sa + z * ca
        x, z = x * cb + z * sb, -x * sb + z * cb
        x, y = x * cc - y * sc, x * sc + y * cc
        p = 1.0 + z * 0.10
        pts.append((cx + x * k * p, cy + y * k * p, z))
    faces = []
    for f in _CF:
        zc = sum(pts[i][2] for i in f) * 0.25
        faces.append((zc, f))
    faces.sort(key=lambda t: t[0])
    for zc, f in faces:
        poly = [(pts[i][0], pts[i][1]) for i in f]
        lum = max(0.0, min(1.0, (zc + 1.8) / 3.6))
        if fill:
            pygame.draw.polygon(dst, scale_color(color, (0.07 + 0.20 * lum) * bright), poly)
        pygame.draw.polygon(dst, scale_color(color, (0.45 + 0.55 * lum) * bright), poly, w)


def _ngon(cx, cy, r, n, rot):
    return [(cx + r * math.cos(rot + i * math.tau / n), cy + r * math.sin(rot + i * math.tau / n))
            for i in range(n)]


def _clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def draw_target(dst, t, sx, sy, sr, now, locked=False):
    if sx < -300 or sx > C.W + 300 or sy < -300 or sy > C.H + 300:
        return
    near = _clamp(1.0 - t.z / C.Z_FAR)
    fade = min(1.0, t.age / 0.35) * (0.55 + 0.45 * min(1.0, near * 2.0 + 0.15))
    col = t.color
    if t.flash > 0:
        col = mix(col, C.WHITE, min(1.0, t.flash / 0.12))
    c = scale_color(col, fade)
    r = max(2.5, sr)
    w = max(1, int(r * 0.09))
    ix, iy = int(sx), int(sy)
    k = t.kind
    ph = t.phase + t.age * 0.0
    ang = (now * 0.9 + ph, now * 1.35 + ph * 1.7, now * 0.55 + ph * 0.6)

    add_glow(dst, sx, sy, col, r * 2.4, 0.6 * fade)

    if k in ("timewarp", "overcharge", "repair"):
        # power-ups are round "orbs" so they read differently from the cube enemies
        pygame.draw.circle(dst, scale_color(col, 0.16 * fade), (ix, iy), int(r))
        pygame.draw.circle(dst, c, (ix, iy), int(r), w + 1)
        rect = pygame.Rect(0, 0, int(r * 2.5), int(r * 2.5))
        rect.center = (ix, iy)
        for i in range(3):
            a0 = now * 2.2 + i * math.tau / 3
            pygame.draw.arc(dst, c, rect, a0, a0 + 0.9, max(1, w))
        if k == "timewarp":                      # clock hands
            pygame.draw.line(dst, c, (sx, sy), (sx, sy - r * 0.55), max(2, w + 1))
            pygame.draw.line(dst, c, (sx, sy), (sx + r * 0.4, sy), max(2, w + 1))
        elif k == "overcharge":                  # bolt
            pts = [(sx + r * 0.12, sy - r * 0.62), (sx - r * 0.3, sy + r * 0.06),
                   (sx + r * 0.03, sy + r * 0.06), (sx - r * 0.12, sy + r * 0.62),
                   (sx + r * 0.32, sy - r * 0.1), (sx, sy - r * 0.1)]
            pygame.draw.polygon(dst, c, pts)
        else:                                    # plus
            pygame.draw.line(dst, c, (sx - r * 0.42, sy), (sx + r * 0.42, sy), max(3, w + 2))
            pygame.draw.line(dst, c, (sx, sy - r * 0.42), (sx, sy + r * 0.42), max(3, w + 2))
    elif k == "standard":
        draw_cube(dst, sx, sy, r, ang, col, w, bright=fade)
        for i in range(4):                       # target-lock ticks around the cube
            a = now * 1.1 + i * math.pi / 2
            pygame.draw.line(dst, scale_color(col, 0.7 * fade),
                             (sx + math.cos(a) * r * 1.12, sy + math.sin(a) * r * 1.12),
                             (sx + math.cos(a) * r * 1.38, sy + math.sin(a) * r * 1.38), max(1, w))
    elif k == "fast":
        dx, dy = sx - C.CX, sy - C.CY
        d = math.hypot(dx, dy) or 1.0
        ux, uy = dx / d, dy / d
        tail = r * 3.6
        for off, a in ((0.0, 0.55), (-0.5, 0.3), (0.5, 0.3)):
            px, py = -uy * r * off, ux * r * off
            pygame.draw.line(dst, scale_color(col, fade * a), (sx + px, sy + py),
                             (sx + px - ux * tail, sy + py - uy * tail), max(1, w))
        draw_cube(dst, sx, sy, r, (ang[0] * 2.2, ang[1] * 0.4, ang[2]), col, w,
                  scale=(0.62, 0.62, 1.55), bright=fade)
    elif k == "small":
        draw_cube(dst, sx, sy, r, ang, col, w, bright=fade)
        for i in range(3):
            a = now * 2.6 + ph + i * math.tau / 3
            pygame.draw.circle(dst, scale_color(C.WHITE, fade),
                               (int(sx + math.cos(a) * r * 1.5), int(sy + math.sin(a) * r * 1.5)),
                               max(1, int(r * 0.14)))
    elif k == "splitter":
        draw_cube(dst, sx, sy, r, ang, col, w, bright=fade)
        draw_cube(dst, sx, sy, r * 0.5, (-ang[0], -ang[1] * 1.4, ang[2]), C.WHITE, max(1, w - 1),
                  bright=fade)
    elif k == "mini":
        draw_cube(dst, sx, sy, r, ang, col, w, bright=fade)
    elif k == "shielded":
        draw_cube(dst, sx, sy, r * 0.75, ang, col, w, bright=fade)
        if t.hp >= 2:
            rect = pygame.Rect(0, 0, int(r * 2.2), int(r * 2.2))
            rect.center = (ix, iy)
            for i in range(3):
                a0 = now * 1.2 + i * math.tau / 3
                pygame.draw.arc(dst, scale_color(C.WHITE, fade), rect, a0, a0 + 1.55, max(2, w + 1))
        else:                                    # cracked
            pygame.draw.line(dst, c, (sx - r * 0.5, sy - r * 0.2), (sx + r * 0.2, sy + r * 0.5), 1)
            pygame.draw.line(dst, c, (sx + r * 0.1, sy - r * 0.6), (sx - r * 0.1, sy + r * 0.1), 1)
    elif k == "mine":
        pulse = 0.65 + 0.35 * math.sin(now * 9 + t.phase)
        cm = scale_color(col, fade * pulse)
        pts = []
        n = 8
        for i in range(n * 2):
            rr = r * (1.0 if i % 2 == 0 else 0.5)
            a = now * 0.9 + t.phase + i * math.pi / n
            pts.append((sx + math.cos(a) * rr, sy + math.sin(a) * rr))
        pygame.draw.polygon(dst, scale_color(col, 0.2 * fade * pulse), pts)
        pygame.draw.polygon(dst, cm, pts, w)
        pygame.draw.circle(dst, cm, (ix, iy), max(2, int(r * 0.28)))
        add_glow(dst, sx, sy, C.RED, r * 3.0, 0.45 * pulse * fade)
    elif k == "node":
        draw_cube(dst, sx, sy, r, (ang[0] * 1.6, ang[1] * 1.6, ang[2]), col, w, bright=fade)
        add_glow(dst, sx, sy, C.GOLD, r * 3.2, 0.55 * fade)
    elif k == "boss":
        pulse = 0.7 + 0.3 * math.sin(now * 3.0)
        draw_cube(dst, sx, sy, r, ang, col, max(2, w), fill=False, bright=fade)
        draw_cube(dst, sx, sy, r * 0.62, (-ang[0] * 1.3, ang[1] * 1.1, -ang[2]), col, max(2, w),
                  bright=fade)
        pygame.draw.polygon(dst, scale_color(col, fade * 0.5), _ngon(sx, sy, r * 1.15, 6, now * 0.3), max(1, w - 1))
        pygame.draw.circle(dst, scale_color(C.WHITE, fade * pulse), (ix, iy), max(2, int(r * 0.16)))
        add_glow(dst, sx, sy, C.RED, r * 3.2, 0.6 * pulse * fade)

    # about to get past you
    if t.z < 7.5 and t.counts_as_leak and k != "node":
        p = 0.5 + 0.5 * math.sin(now * 14)
        pygame.draw.circle(dst, scale_color(C.RED, 0.35 + 0.5 * p), (ix, iy), int(r * 1.45 + 4), 2)

    if locked:
        b = r * 1.5 + 6
        L = max(6, b * 0.35)
        gc = C.GREEN
        for sxn, syn in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            cx, cy = sx + sxn * b, sy + syn * b
            pygame.draw.line(dst, gc, (cx, cy), (cx - sxn * L, cy), 2)
            pygame.draw.line(dst, gc, (cx, cy), (cx, cy - syn * L), 2)


def draw_boss_tethers(dst, boss, world, now):
    sx, sy, _ = world.project(boss)
    for n in boss.children:
        if n.alive:
            nx, ny, _ = world.project(n)
            pygame.draw.line(dst, scale_color(C.GOLD, 0.45), (sx, sy), (nx, ny), 1)


def draw_reticle(dst, x, y, now, fist, locked, charge, charge_ready, overcharge,
                 present=True, pulse=0.0, lock_prog=0.0, tint=None):
    col = C.GREEN if locked else (tint or C.CYAN)
    if not present:
        col = C.GREY
    ix, iy = int(x), int(y)
    add_glow(dst, x, y, col, 46, 0.45 if present else 0.15)
    if not fist:
        R = 30
        rect = pygame.Rect(0, 0, R * 2, R * 2)
        rect.center = (ix, iy)
        for i in range(4):
            a0 = now * 1.6 + i * math.pi / 2
            pygame.draw.arc(dst, col, rect, a0, a0 + 0.95, 3)
        for i in range(4):
            a = i * math.pi / 2 + math.pi / 4
            pygame.draw.line(dst, col, (x + math.cos(a) * 11, y + math.sin(a) * 11),
                             (x + math.cos(a) * 19, y + math.sin(a) * 19), 2)
    else:
        R = 17
        L = 9
        for sxn, syn in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            cx, cy = x + sxn * R, y + syn * R
            pygame.draw.line(dst, C.WHITE, (cx, cy), (cx - sxn * L, cy), 3)
            pygame.draw.line(dst, C.WHITE, (cx, cy), (cx, cy - syn * L), 3)
    pygame.draw.circle(dst, C.WHITE if fist else col, (ix, iy), 3)
    if pulse > 0:
        pygame.draw.circle(dst, scale_color(C.WHITE, pulse), (ix, iy), int(22 + 40 * (1 - pulse)), 2)
    if overcharge:
        for sgn in (-1, 1):
            pygame.draw.circle(dst, C.PURPLE, (int(x + sgn * C.OVERCHARGE_SPREAD), iy), 12, 2)
    # auto-fire lock-on progress
    if lock_prog > 0.02:
        rect = pygame.Rect(0, 0, 76, 76)
        rect.center = (ix, iy)
        pygame.draw.arc(dst, C.GREEN, rect, math.pi / 2, math.pi / 2 + math.tau * min(1.0, lock_prog), 5)
    # charged-shot ring
    if charge > 0.02 or charge_ready:
        rect = pygame.Rect(0, 0, 100, 100)
        rect.center = (ix, iy)
        if charge_ready:
            p = 0.6 + 0.4 * math.sin(now * 16)
            pygame.draw.circle(dst, scale_color(C.GOLD, p), (ix, iy), 50, 3)
            add_glow(dst, x, y, C.GOLD, 80, 0.5 * p)
        else:
            pygame.draw.arc(dst, C.ORANGE, rect, math.pi / 2, math.pi / 2 + math.tau * charge, 4)


def draw_heart(dst, x, y, size, color, filled=True):
    pts = []
    for i in range(28):
        t = i * math.tau / 28
        hx = 16 * math.sin(t) ** 3
        hy = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((x + hx * size / 34, y + hy * size / 34))
    if filled:
        add_glow(dst, x, y, color, size * 1.1, 0.5)
        pygame.draw.polygon(dst, color, pts)
    else:
        pygame.draw.polygon(dst, scale_color(color, 0.35), pts, 2)


def draw_bar(dst, x, y, w, h, frac, color, back=(25, 35, 55)):
    pygame.draw.rect(dst, back, (x, y, w, h), border_radius=h // 2)
    fw = int(w * _clamp(frac))
    if fw > 0:
        pygame.draw.rect(dst, color, (x, y, fw, h), border_radius=h // 2)
    pygame.draw.rect(dst, scale_color(color, 0.6), (x, y, w, h), 1, border_radius=h // 2)


def draw_hud_panel(dst, rect, color=C.CYAN, alpha=165, cut=14):
    """Chamfered translucent HUD plate with a neon edge and corner accents."""
    x, y, w, h = rect
    poly = [(x + cut, y), (x + w, y), (x + w, y + h - cut), (x + w - cut, y + h),
            (x, y + h), (x, y + cut)]
    s = pygame.Surface((w + 2, h + 2), pygame.SRCALPHA)
    local = [(px - x, py - y) for px, py in poly]
    pygame.draw.polygon(s, (5, 8, 24, alpha), local)
    dst.blit(s, (x, y))
    pygame.draw.polygon(dst, scale_color(color, 0.55), poly, 2)
    # bright corner ticks
    pygame.draw.line(dst, color, (x + cut, y), (x + cut + 22, y), 3)
    pygame.draw.line(dst, color, (x + w, y + h - cut), (x + w - 22 + 0, y + h - cut + 0), 1)
    pygame.draw.line(dst, color, (x + w - cut - 22, y + h), (x + w - cut, y + h), 3)
