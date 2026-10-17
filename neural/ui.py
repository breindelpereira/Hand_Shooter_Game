"""Hand-operated UI: a button is 'pressed' by closing your fist / pinching on it, or by dwelling.

Buttons lean toward the reticle as it approaches, glow, and 'pop' when chosen.
"""
from __future__ import annotations

import math

import pygame

from . import config as C
from .fx import add_glow, draw_text, scale_color


def _rect_dist(r: pygame.Rect, x: float, y: float) -> float:
    dx = max(r.left - x, 0, x - r.right)
    dy = max(r.top - y, 0, y - r.bottom)
    return math.hypot(dx, dy)


class Button:
    def __init__(self, rect, label, action=None, color=C.CYAN, size=34, dwell=C.DWELL_TIME,
                 sub: str | None = None):
        self.rect = pygame.Rect(rect)
        self.label = label
        self.action = action
        self.color = color
        self.size = size
        self.dwell = dwell
        self.sub = sub
        self.progress = 0.0
        self.hot = 0.0          # smooth hover glow
        self.near = 0.0         # 0..1: how close the reticle is (the button leans in)
        self.pop = 0.0          # 1 -> 0 after being chosen
        self.selected = False   # drawn as the active option of a choice row
        self.enabled = True

    def hovered(self, x, y) -> bool:
        return self.rect.inflate(10, 10).collidepoint(x, y)

    def update(self, dt, inp, locked=False) -> bool:
        """Returns True the frame the button is activated."""
        usable = self.enabled and not locked and inp.present
        over = usable and self.hovered(inp.x, inp.y)
        goal = max(0.0, 1.0 - _rect_dist(self.rect, inp.x, inp.y) / 170.0) if usable else 0.0
        self.near += (goal - self.near) * min(1.0, dt * 10.0)
        self.pop = max(0.0, self.pop - dt * 4.0)
        if over:
            self.progress += dt / self.dwell
            self.hot = min(1.0, self.hot + dt * 8)
        else:
            self.progress = max(0.0, self.progress - dt * 3.0)
            self.hot = max(0.0, self.hot - dt * 6)
        clicked = (self.enabled and not locked and inp.fire and
                   self.rect.inflate(10, 10).collidepoint(inp.fire_x, inp.fire_y))
        if clicked or self.progress >= 1.0:
            self.progress = 0.0
            self.pop = 1.0
            return True
        return False

    def draw(self, dst, now):
        col = self.color if self.enabled else C.GREY
        lean = max(self.hot, self.near * 0.7)
        pulse = 0.5 + 0.5 * math.sin(now * 7.0)
        grow = int(self.near * 8 + self.pop * 16)
        r = self.rect.inflate(grow, int(grow * 0.6))
        panel = pygame.Surface(r.size, pygame.SRCALPHA)
        if self.selected:
            fill = (int(col[0] * 0.28), int(col[1] * 0.28), int(col[2] * 0.28), 235)
        else:
            fill = (8, 14, 34, 190)
        pygame.draw.rect(panel, fill, panel.get_rect(), border_radius=14)
        if self.pop > 0:
            pygame.draw.rect(panel, (*col, int(110 * self.pop)), panel.get_rect(), border_radius=14)
        dst.blit(panel, r.topleft)
        glow = 0.22 * self.hot + 0.16 * self.near * (0.6 + 0.4 * pulse) + 0.3 * self.pop
        if self.selected:
            glow += 0.10
        if glow > 0.02:
            add_glow(dst, r.centerx, r.centery, col, max(r.w, r.h) * 0.75, min(1.0, glow))
        width = 4 if self.selected else 2 + int(lean * 2)
        pygame.draw.rect(dst, scale_color(col, 0.55 + 0.45 * max(lean, 1.0 if self.selected else 0.0)),
                         r, width, border_radius=14)
        if self.progress > 0:
            w = int((r.w - 24) * min(1.0, self.progress))
            pygame.draw.rect(dst, col, (r.x + 12, r.bottom - 10, w, 4), border_radius=2)
        tcol = C.WHITE if self.selected else col
        if self.sub:
            draw_text(dst, self.label, self.size, tcol, (r.centerx, r.centery - 9), glow=lean > 0.3 or self.selected)
            draw_text(dst, self.sub, 18, C.GREY if not self.selected else C.WHITE, (r.centerx, r.centery + 21),
                      glow=False)
        elif self.label:
            draw_text(dst, self.label, self.size, tcol, r.center, glow=lean > 0.3 or self.selected)


class BigButton:
    """A big round target (the PLAY circle). Hold your open hand in it, or close your fist / pinch."""

    def __init__(self, center, radius, label, action=None, color=C.CYAN, dwell=C.PLAY_DWELL):
        self.cx, self.cy = center
        self.radius = radius
        self.label = label
        self.sub = ""
        self.action = action
        self.color = color
        self.dwell = dwell
        self.progress = 0.0
        self.hot = 0.0
        self.near = 0.0
        self.pop = 0.0

    def _inside(self, x, y) -> bool:
        return math.hypot(x - self.cx, y - self.cy) <= self.radius + 14

    def update(self, dt, inp, locked=False) -> bool:
        usable = not locked and inp.present
        over = usable and self._inside(inp.x, inp.y)
        goal = max(0.0, 1.0 - max(0.0, math.hypot(inp.x - self.cx, inp.y - self.cy) - self.radius) / 260.0) \
            if usable else 0.0
        self.near += (goal - self.near) * min(1.0, dt * 8.0)
        self.pop = max(0.0, self.pop - dt * 3.0)
        if over:
            self.progress += dt / self.dwell
            self.hot = min(1.0, self.hot + dt * 6)
        else:
            self.progress = max(0.0, self.progress - dt * 2.0)
            self.hot = max(0.0, self.hot - dt * 5)
        clicked = usable and inp.fire and self._inside(inp.fire_x, inp.fire_y)
        if clicked or self.progress >= 1.0:
            self.progress = 0.0
            self.pop = 1.0
            return True
        return False

    def draw(self, dst, now):
        col = self.color
        pulse = 0.5 + 0.5 * math.sin(now * 3.2)
        R = int(self.radius * (1.0 + self.near * 0.05 + self.pop * 0.12 + 0.02 * pulse))
        c = (int(self.cx), int(self.cy))
        add_glow(dst, c[0], c[1], col, R * 2.1, 0.25 + 0.25 * self.hot + 0.2 * pulse * (1 - self.hot))
        # two slowly expanding rings invite you in
        for k in range(2):
            ph = (now * 0.5 + k * 0.5) % 1.0
            pygame.draw.circle(dst, scale_color(col, 0.5 * (1 - ph)), c, int(R * (1.0 + 0.45 * ph)), 2)
        pygame.draw.circle(dst, (6, 12, 32), c, R)
        pygame.draw.circle(dst, scale_color(col, 0.55 + 0.45 * max(self.hot, self.near)), c, R, 5)
        if self.progress > 0:
            rect = pygame.Rect(0, 0, int(R * 2 + 26), int(R * 2 + 26))
            rect.center = c
            pygame.draw.arc(dst, C.WHITE, rect, math.pi / 2, math.pi / 2 + math.tau * min(1.0, self.progress), 8)
        draw_text(dst, self.label, 76, C.WHITE, (c[0], c[1] - 10))
        if self.sub:
            draw_text(dst, self.sub, 20, getattr(self, "sub_color", None) or col, (c[0], c[1] + 52),
                      glow=False)


def update_buttons(buttons, dt, inp, locked, audio=None):
    """Update a group of buttons; returns the one chosen this frame (or None).
    Plays a soft tick as the reticle first touches a button and a 'select' when one is chosen."""
    chosen = None
    for b in buttons:
        was = b.hot
        if b.update(dt, inp, locked) and chosen is None:
            chosen = b
        if audio is not None and was < 0.05 < b.hot:
            audio.play_ui("hover", 0.5)
    if chosen is not None and audio is not None:
        audio.play_ui("select", 0.9)
    return chosen


def draw_panel(dst, rect, alpha=170, border=C.CYAN):
    r = pygame.Rect(rect)
    s = pygame.Surface(r.size, pygame.SRCALPHA)
    pygame.draw.rect(s, (6, 10, 28, alpha), s.get_rect(), border_radius=16)
    dst.blit(s, r.topleft)
    pygame.draw.rect(dst, scale_color(border, 0.5), r, 2, border_radius=16)
