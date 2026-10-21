"""Neon canyon scenery.

Sky + planet + skyline, a perspective grid floor, rows of tower blocks, floating cubes and
glowing gates.  Everything is a pure function of the scroll distance (no per-object state),
so it is cheap, deterministic and never runs out of track.
"""
from __future__ import annotations

import math
import random

import numpy as np
import pygame

from . import config as C
from .fx import mix, scale_color

F = C.FOCAL
GROUND = 2.0            # world y of the floor (+y is down)
ZMAX = 64.0
ZFOG = 58.0
CELL = 3.9              # spacing of tower cells along z
ZBOX = 54.0             # blocks further than this are not drawn
GATE_SP = 26.0
GRID_SP = 2.0

# blocks: three accent colours; grid_a/grid_b: floor lines; sky_*: gradient; fog: distance colour
THEMES = [
    dict(name="neon city", blocks=[(0, 210, 255), (255, 50, 210), (130, 100, 255)],
         grid_a=(0, 170, 255), grid_b=(255, 60, 210), sky_top=(4, 3, 20), sky_hor=(46, 16, 88),
         fog=(26, 11, 58), gate=(0, 229, 255)),
    dict(name="toxic", blocks=[(60, 255, 140), (0, 220, 200), (190, 255, 70)],
         grid_a=(40, 255, 150), grid_b=(0, 200, 220), sky_top=(2, 9, 12), sky_hor=(8, 62, 50),
         fog=(6, 30, 28), gate=(90, 255, 160)),
    dict(name="inferno", blocks=[(255, 120, 40), (255, 50, 70), (255, 205, 60)],
         grid_a=(255, 100, 30), grid_b=(255, 40, 90), sky_top=(14, 2, 5), sky_hor=(82, 18, 12),
         fog=(46, 11, 11), gate=(255, 150, 50)),
    dict(name="void", blocks=[(175, 105, 255), (80, 140, 255), (255, 80, 230)],
         grid_a=(150, 90, 255), grid_b=(70, 130, 255), sky_top=(6, 2, 20), sky_hor=(56, 20, 100),
         fog=(30, 12, 66), gate=(195, 125, 255)),
    dict(name="ice", blocks=[(150, 230, 255), (225, 245, 255), (80, 160, 255)],
         grid_a=(120, 200, 255), grid_b=(200, 240, 255), sky_top=(2, 6, 18), sky_hor=(22, 56, 100),
         fog=(14, 28, 54), gate=(175, 232, 255)),
    dict(name="clock it", blocks=[(255, 95, 205), (185, 105, 255), (255, 150, 235)],
         grid_a=(255, 85, 205), grid_b=(175, 105, 255), sky_top=(18, 3, 26), sky_hor=(104, 28, 122),
         fog=(58, 16, 78), gate=(255, 115, 225)),
]
PINK_THEME = 5                    # "clock mode" world
_KEYS = ("grid_a", "grid_b", "sky_top", "sky_hor", "fog", "gate")


def _lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def _ic(c):
    return (max(0, min(255, int(c[0]))), max(0, min(255, int(c[1]))), max(0, min(255, int(c[2]))))


class Tunnel:
    """Name kept from the original tunnel so scenes don't change."""

    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random(5)
        t0 = THEMES[0]
        self.cur = {k: tuple(float(v) for v in t0[k]) for k in _KEYS}
        self.cur["blocks"] = [tuple(float(v) for v in c) for c in t0["blocks"]]
        self.target = t0
        self.theme_idx = 0
        self._cells: dict = {}
        self._bg = pygame.Surface((C.W, C.H)).convert()
        self._bg_key = None
        self.vign = self._make_vignette()
        self._planet = self._make_planet()
        self._skyline = self._make_skyline()
        self._stars = [(self.rng.randrange(0, C.W), self.rng.randrange(0, C.CY - 30),
                        self.rng.uniform(0.3, 1.0), self.rng.uniform(0, 6.28))
                       for _ in range(110)]
        self.motes = [[self._mote_x(), self.rng.uniform(-3.4, 1.8), self.rng.uniform(2, 40)]
                      for _ in range(70)]
        self.clock = 0.0
        self.cx = float(C.CX)      # vanishing point x: sways slightly opposite to the hand

    # ---- theme -----------------------------------------------------------
    def set_theme(self, idx: int):
        self.theme_idx = idx % len(THEMES)
        self.target = THEMES[self.theme_idx]

    def _blend(self, dt):
        k = min(1.0, dt * 1.1)                # a smooth ~2 s cross-fade between colour worlds
        tg = self.target
        for key in _KEYS:
            self.cur[key] = _lerp(self.cur[key], tg[key], k)
        self.cur["blocks"] = [_lerp(a, b, k) for a, b in zip(self.cur["blocks"], tg["blocks"])]

    def _mote_x(self):
        x = self.rng.uniform(1.9, 7.0)
        return x if self.rng.random() < 0.5 else -x

    # ---- precomputed art ---------------------------------------------------
    @staticmethod
    def _make_vignette():
        ys, xs = np.mgrid[0:C.H, 0:C.W]
        d = np.hypot((xs - C.CX) / (C.W * 0.5), (ys - C.CY) / (C.H * 0.5))
        a = np.clip((d - 0.55) / 0.75, 0.0, 1.0) ** 1.5 * 255
        s = pygame.Surface((C.W, C.H), pygame.SRCALPHA)
        px = pygame.surfarray.pixels3d(s)
        px[:] = (255, 30, 50)
        del px
        al = pygame.surfarray.pixels_alpha(s)
        al[:] = a.T.astype(np.uint8)
        del al
        return s

    @staticmethod
    def _make_planet(radius: int = 110):
        n = radius * 2 + 4
        ys, xs = np.mgrid[0:n, 0:n]
        x = (xs - n / 2 + 0.5) / radius
        y = (ys - n / 2 + 0.5) / radius
        r2 = x * x + y * y
        inside = r2 <= 1.0
        z = np.sqrt(np.clip(1.0 - r2, 0, 1))
        lx, ly, lz = 0.55, -0.45, 0.70
        lam = np.clip(x * lx + y * ly + z * lz, 0, 1)
        bands = 0.82 + 0.18 * np.sin(y * 17.0 + np.sin(x * 5.0))
        lum = np.clip(0.18 + 0.82 * lam ** 1.1, 0, 1) * bands
        rgb = (lum * 255).astype(np.uint8)
        arr = np.stack([rgb, rgb, rgb], axis=-1)
        surf = pygame.surfarray.make_surface(arr.swapaxes(0, 1)).convert_alpha()
        alpha = pygame.surfarray.pixels_alpha(surf)
        edge = np.clip((1.0 - np.sqrt(r2)) * radius, 0, 1)
        alpha[:] = (edge * 255).astype(np.uint8).T
        del alpha
        return surf

    def _make_skyline(self):
        h = 150
        s = pygame.Surface((C.W + 160, h), pygame.SRCALPHA)
        rng = random.Random(99)
        x = -10
        while x < C.W + 170:
            w = rng.randint(22, 70)
            bh = int(rng.choice((18, 28, 40, 55, 75, 100, 130)) * rng.uniform(0.7, 1.15))
            col = (7, 5, 20, 235)
            pygame.draw.rect(s, col, (x, h - bh, w, bh))
            pygame.draw.line(s, (60, 50, 120, 90), (x, h - bh), (x + w, h - bh), 1)
            if rng.random() < 0.35:                                  # antenna
                pygame.draw.line(s, (60, 50, 120, 110), (x + w // 2, h - bh), (x + w // 2, h - bh - 14), 1)
            for wy in range(h - bh + 6, h - 4, 9):
                for wx in range(x + 5, x + w - 5, 8):
                    if rng.random() < 0.13:
                        s.set_at((wx, wy), (170, 190, 255, 90))
            x += w + rng.randint(0, 6)
        return s

    # ---- cell generation -----------------------------------------------------
    def _cell(self, i: int):
        c = self._cells.get(i)
        if c is not None:
            return c
        rnd = random.Random(i * 10007 + 17)
        boxes = []
        for side in (-1, 1):
            if rnd.random() < 0.14:
                continue
            inner = rnd.uniform(2.9, 4.8)
            w = rnd.uniform(1.2, 2.9)
            r = rnd.random()
            h = (rnd.uniform(0.9, 2.6) if r < 0.3 else
                 rnd.uniform(2.6, 6.0) if r < 0.75 else rnd.uniform(6.0, 11.0))
            d = rnd.uniform(1.6, 2.8)
            x0, x1 = (-inner - w, -inner) if side < 0 else (inner, inner + w)
            zoff = rnd.uniform(0, CELL - d)
            ci = rnd.randrange(3)
            bits = (rnd.getrandbits(64) & rnd.getrandbits(64)) | (rnd.getrandbits(64) & rnd.getrandbits(64))
            boxes.append((x0, x1, GROUND - h, GROUND, zoff, d, ci, bits, 0))
            if h > 3 and rnd.random() < 0.45:                       # stepped cap block
                w2 = w * rnd.uniform(0.45, 0.7)
                h2 = rnd.uniform(0.6, 1.7)
                cx = (x0 + x1) / 2
                boxes.append((cx - w2 / 2, cx + w2 / 2, GROUND - h - h2, GROUND - h,
                              zoff + 0.25, d - 0.5, (ci + 1) % 3, bits >> 7, 0))
        if rnd.random() < 0.65:                                      # floating cube
            side = rnd.choice((-1, 1))
            s = rnd.uniform(0.35, 0.85)
            x = side * rnd.uniform(2.3, 5.6)
            y = rnd.uniform(-3.4, 0.7)
            boxes.append((x - s / 2, x + s / 2, y - s / 2, y + s / 2,
                          rnd.uniform(0, CELL - s), s, rnd.randrange(3), rnd.getrandbits(64), 1))
        if len(self._cells) > 160:
            for k in [k for k in self._cells if k < i - 6]:
                del self._cells[k]
        self._cells[i] = boxes
        return boxes

    # ---- drawing -------------------------------------------------------------
    def _render_bg(self, th):
        key = (_ic(th["sky_top"]), _ic(th["sky_hor"]), _ic(th["fog"]))
        if key == self._bg_key:
            return
        self._bg_key = key
        bg = self._bg
        top, hor, fog = th["sky_top"], th["sky_hor"], th["fog"]
        step = 6
        for y in range(0, C.CY, step):
            t = (y / C.CY) ** 1.8
            bg.fill(_ic(_lerp(top, hor, t)), (0, y, C.W, step))
        near = scale_color(_ic(fog), 0.55)
        bottom = (3, 3, 11)
        for y in range(C.CY, C.H, step):
            t = ((y - C.CY) / (C.H - C.CY)) ** 0.6
            bg.fill(_ic(_lerp(near, bottom, t)), (0, y, C.W, step))

    def draw(self, dst, scroll: float, speed: float = 1.0, tint=None, dt: float = 1 / 60,
             sway: float = 0.0):
        """sway in -1..1 (hand position across the screen): the scene leans the other way,
        which sells the depth for almost no cost."""
        self.clock += dt
        self._blend(dt)
        goal = C.CX - max(-1.0, min(1.0, sway)) * 46.0
        self.cx += (goal - self.cx) * min(1.0, dt * 5.0)
        th = dict(self.cur)
        if tint is not None:                       # time-warp etc. recolour the world
            for k in ("grid_a", "grid_b", "gate"):
                th[k] = _lerp(th[k], tint, 0.7)
        self._render_bg(th)
        dst.blit(self._bg, (0, 0))
        now = self.clock

        # stars
        for x, y, b, ph in self._stars:
            v = b * (0.65 + 0.35 * math.sin(now * 1.7 + ph))
            c = (int(150 * v), int(165 * v), int(255 * v))
            dst.fill(c, (x, y, 2 if b > 0.7 else 1, 2 if b > 0.7 else 1))

        # planet (tinted by theme) + ring
        pl = self._planet.copy()
        tcol = _ic(_lerp((40, 40, 60), th["blocks"][2], 0.55))
        pl.fill((int(tcol[0] * 0.55), int(tcol[1] * 0.55), int(tcol[2] * 0.55)), special_flags=pygame.BLEND_RGB_MULT)
        px, py = C.CX + 330 + (self.cx - C.CX) * 0.5, C.CY - 185
        dst.blit(pl, pl.get_rect(center=(px, py)))
        ring = pygame.Rect(0, 0, 330, 70)
        ring.center = (px, py + 6)
        pygame.draw.ellipse(dst, _ic(_lerp(th["fog"], th["blocks"][2], 0.5)), ring, 3)

        # skyline silhouette on the horizon
        dst.blit(self._skyline, (-70 + int((self.cx - C.CX) * 0.6), C.CY - self._skyline.get_height() + 2))
        # horizon glow
        for j in range(8):
            c = scale_color(_ic(th["gate"]), 0.30 * (1 - j / 8))
            dst.fill(c, (0, C.CY - 1 - j, C.W, 1), special_flags=pygame.BLEND_RGB_ADD)
        pygame.draw.line(dst, scale_color(_ic(th["gate"]), 0.85), (0, C.CY), (C.W, C.CY), 2)

        self._floor(dst, th, scroll)
        self._objects(dst, th, scroll)
        self._motes(dst, th, speed, dt)

    def _floor(self, dst, th, scroll):
        fog = th["fog"]
        # longitudinal lines
        hz = C.CY + GROUND * F / ZMAX
        for n in range(-13, 14):
            x = n * 1.25
            col = _ic(_lerp(th["grid_a"], fog, 0.55 if n % 4 else 0.25))
            pygame.draw.line(dst, scale_color(col, 0.75 if n % 4 else 1.0),
                             (self.cx + x * F / ZMAX, hz), (self.cx + x * (F / 3.33), C.H), 1)
        # transverse lines scroll toward the player
        base = int(scroll // GRID_SP)
        for k in range(1, int(ZMAX / GRID_SP)):
            idx = base + k
            z = idx * GRID_SP - scroll
            y = C.CY + GROUND * F / z
            if y >= C.H or z < 1.0:
                continue
            t = min(1.0, z / ZFOG) ** 0.7
            accent = idx % 4 == 0
            col = _lerp(th["grid_b"] if accent else th["grid_a"], fog, t)
            col = scale_color(_ic(col), (0.9 if accent else 0.6) * (1 - t * 0.6))
            pygame.draw.line(dst, col, (0, y), (C.W, y), 2 if z < 14 else 1)

    def _objects(self, dst, th, scroll):
        items = []
        i0 = int(math.floor((scroll - 3) / CELL))
        i1 = int(math.floor((scroll + ZBOX) / CELL)) + 1
        for i in range(i0, i1 + 1):
            for b in self._cell(i):
                z = i * CELL + b[4] - scroll
                if -b[5] < z < ZBOX:
                    items.append((z, 0, b))
        j0 = int(math.ceil(scroll / GATE_SP))
        j = j0
        while True:
            z = j * GATE_SP - scroll
            if z > ZMAX:
                break
            if z > 2.5:
                items.append((z, 1, j))
            j += 1
        items.sort(key=lambda it: -it[0])
        blocks = th["blocks"]
        for z, kind, data in items:
            if kind == 0:
                x0, x1, y0, y1, _zo, d, ci, bits, fl = data
                if fl:
                    bob = math.sin(scroll * 0.35 + (bits & 63)) * 0.28
                    y0 += bob
                    y1 += bob
                self._box(dst, x0, x1, y0, y1, z, d, blocks[ci], bits, th, fl)
            else:
                self._gate(dst, z, th, data)

    def _box(self, dst, x0, x1, y0, y1, z, d, col, bits, th, floating):
        zf = z if z > 1.0 else 1.0
        zb = z + d
        if zb < 1.2:
            return
        kf, kb = F / zf, F / zb
        X0f, X1f = self.cx + x0 * kf, self.cx + x1 * kf
        Y0f, Y1f = C.CY + y0 * kf, C.CY + y1 * kf
        X0b, X1b = self.cx + x0 * kb, self.cx + x1 * kb
        Y0b, Y1b = C.CY + y0 * kb, C.CY + y1 * kb
        if max(X0f, X1f, X0b, X1b) < -40 or min(X0f, X1f, X0b, X1b) > C.W + 40:
            return
        if max(Y1f, Y1b) < -40 or min(Y0f, Y0b) > C.H + 40:
            return
        fr, fg, fb = th["fog"]
        t = (zf / ZFOG) ** 0.6
        if t > 1.0:
            t = 1.0
        u = 1.0 - t
        cr, cg, cb = col
        edge = (int(cr * u + fr * t), int(cg * u + fg * t), int(cb * u + fb * t))
        # dark body tinted by the block colour, drifting to the fog colour with distance
        t9 = t * 0.9
        u9 = 1.0 - t9
        br, bg, bb = cr * 0.075 + 5, cg * 0.075 + 4, cb * 0.10 + 14
        front = (int(br * u9 + fr * t9), int(bg * u9 + fg * t9), int(bb * u9 + fb * t9))
        side = (int(front[0] * 0.72), int(front[1] * 0.72), int(front[2] * 0.72))
        lw = 2 if t < 0.65 else 1
        near = t < 0.55

        if x1 <= 0:                                  # right face is visible
            pts = [(X1f, Y0f), (X1b, Y0b), (X1b, Y1b), (X1f, Y1f)]
            pygame.draw.polygon(dst, side, pts)
            if near:
                pygame.draw.polygon(dst, (edge[0] >> 1, edge[1] >> 1, edge[2] >> 1), pts, 1)
                self._face_stripes(dst, X1f, X1b, Y0f, Y0b, Y1f, Y1b, edge)
        elif x0 >= 0:                                # left face is visible
            pts = [(X0f, Y0f), (X0b, Y0b), (X0b, Y1b), (X0f, Y1f)]
            pygame.draw.polygon(dst, side, pts)
            if near:
                pygame.draw.polygon(dst, (edge[0] >> 1, edge[1] >> 1, edge[2] >> 1), pts, 1)
                self._face_stripes(dst, X0f, X0b, Y0f, Y0b, Y1f, Y1b, edge)
        if y0 >= 0:                                  # we see the top
            pts = [(X0f, Y0f), (X1f, Y0f), (X1b, Y0b), (X0b, Y0b)]
            top = (min(255, int(front[0] * 1.9)), min(255, int(front[1] * 1.9)), min(255, int(front[2] * 1.9)))
            pygame.draw.polygon(dst, top, pts)
            if near:
                pygame.draw.polygon(dst, edge, pts, 1)
        elif y1 <= 0:                                # we see the underside
            pts = [(X0f, Y1f), (X1f, Y1f), (X1b, Y1b), (X0b, Y1b)]
            pygame.draw.polygon(dst, side, pts)
            if near:
                pygame.draw.polygon(dst, (edge[0] >> 1, edge[1] >> 1, edge[2] >> 1), pts, 1)

        rect = pygame.Rect(int(X0f), int(Y0f), max(1, int(X1f - X0f)), max(1, int(Y1f - Y0f)))
        pygame.draw.rect(dst, front, rect)
        # lit windows (only while the block is close enough to read)
        if not floating and rect.w > 26 and t < 0.5:
            cols = max(1, min(4, int((x1 - x0) / 0.6)))
            cw = (x1 - x0) / cols
            tw = min(1.0, 0.25 + t * 1.1)
            wcol = (int((cr * 0.55 + 14) * (1 - tw) + fr * tw), int((cg * 0.55 + 14) * (1 - tw) + fg * tw),
                    int((cb * 0.55 + 14) * (1 - tw) + fb * tw))
            wh = max(2, int(0.26 * kf))
            ww = max(2, int(cw * 0.34 * kf))
            r = 0
            wy = y1 - 0.65
            while wy > y0 + 0.3 and r < 12:
                sy = C.CY + wy * kf
                if sy < -30:
                    break
                if sy < C.H + 30:
                    for c in range(cols):
                        if (bits >> ((r * cols + c) % 64)) & 1:
                            wxw = x0 + (c + 0.5) * cw
                            dst.fill(wcol, (int(self.cx + (wxw - cw * 0.17) * kf), int(sy), ww, wh))
                wy -= 0.8
                r += 1
        pygame.draw.rect(dst, edge, rect, lw)
        if near and rect.w > 20:                     # bright roof strip
            pygame.draw.line(dst, (min(255, edge[0] + 70), min(255, edge[1] + 70), min(255, edge[2] + 70)),
                             (rect.left, rect.top), (rect.right, rect.top), 3)

    @staticmethod
    def _face_stripes(dst, Xf, Xb, Y0f, Y0b, Y1f, Y1b, edge):
        """A couple of light strips running down a side face give it direction."""
        e = (edge[0] // 3, edge[1] // 3, edge[2] // 3)
        for k in (0.33, 0.66):
            ya = Y0f + (Y1f - Y0f) * k
            yb = Y0b + (Y1b - Y0b) * k
            pygame.draw.line(dst, e, (Xf, ya), (Xb, yb), 1)

    def _gate(self, dst, z, th, j):
        k = F / z
        gx, top = 4.7, -3.6
        L, R = self.cx - gx * k, self.cx + gx * k
        T, B = C.CY + top * k, C.CY + GROUND * k
        t = min(1.0, z / ZFOG) ** 0.8
        col = _ic(_lerp(th["gate"], th["fog"], t * 0.95))
        w = max(1, int(5 * k / 60))
        w = min(w, 9)
        dim = scale_color(col, 0.35)
        for off in (w + 3, 0):
            c = col if off == 0 else dim
            pygame.draw.line(dst, c, (L - off, B), (L - off, T - off), w)
            pygame.draw.line(dst, c, (R + off, B), (R + off, T - off), w)
            pygame.draw.line(dst, c, (L - off, T - off), (R + off, T - off), w)
        # light cubes along the top bar
        for n in range(1, 8):
            x = L + (R - L) * n / 8
            s = max(2, int(3 + k * 0.03))
            pygame.draw.rect(dst, _ic(_lerp(th["grid_b"], th["fog"], t)), (x - s, T - s - w - 3, s * 2, s * 2))

    def _motes(self, dst, th, speed, dt):
        gate = th["gate"]
        fog = th["fog"]
        adv = C.BASE_SPEED * max(0.2, speed) * dt
        for m in self.motes:
            m[2] -= adv
            if m[2] < 1.4:
                m[0], m[1], m[2] = self._mote_x(), self.rng.uniform(-3.4, 1.8), self.rng.uniform(34, 46)
            k = F / m[2]
            sx, sy = self.cx + m[0] * k, C.CY + m[1] * k
            if not (0 <= sx < C.W and 0 <= sy < C.H):
                continue
            t = min(1.0, m[2] / 40)
            col = _ic(_lerp(gate, fog, t))
            col = scale_color(col, 0.75 * (1 - t * 0.7))
            ln = 0.02 + 0.05 * speed
            pygame.draw.line(dst, col, (sx, sy), (sx + (sx - self.cx) * ln, sy + (sy - C.CY) * ln),
                             1 if m[2] > 12 else 2)
