"""Input abstraction: the game only ever sees InputState."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

import pygame

from . import config as C


@dataclass
class InputState:
    x: float = C.W / 2
    y: float = C.H / 2
    present: bool = True          # a hand is currently being tracked
    fist: bool = False            # fist is held closed
    fire: bool = False            # fist closed this frame (edge)
    fire_x: float = C.W / 2
    fire_y: float = C.H / 2
    ratio: float = 0.0            # raw curl ratio (debug / calibration)


class InputProvider:
    name = "base"
    has_camera = False

    def start(self): ...
    def stop(self): ...
    def poll(self, dt: float) -> InputState: raise NotImplementedError
    def preview(self):
        """Small RGB numpy frame for the picture-in-picture view, or None."""
        return None
    def set_calibration(self, open_ratio: float, closed_ratio: float) -> bool:
        return True


class MouseInput(InputProvider):
    """Development / emergency fallback: mouse aims, left button (or space) fires."""

    name = "mouse"

    def __init__(self):
        self._prev_down = False

    def poll(self, dt: float) -> InputState:
        mx, my = pygame.mouse.get_pos()
        down = pygame.mouse.get_pressed()[0] or pygame.key.get_pressed()[pygame.K_SPACE]
        fire = down and not self._prev_down
        self._prev_down = down
        return InputState(x=float(mx), y=float(my), present=True, fist=down, fire=fire,
                          fire_x=float(mx), fire_y=float(my), ratio=0.9 if down else 1.8)


class BotInput(InputProvider):
    """Plays by itself (attract mode / automated tests). Needs a World to look at."""

    name = "bot"

    def __init__(self, world, skill: float = 0.85, rng: random.Random | None = None):
        self.world = world
        self.skill = skill
        self.rng = rng or random.Random(7)
        self.x, self.y = C.W / 2.0, C.H / 2.0
        self._fist = False
        self._cool = 0.0
        self._aim_target = None

    def _pick(self):
        best, best_z = None, 1e9
        for t in self.world.targets:
            if not t.alive or t.kind in ("boss", "mine"):
                continue
            if t.z < 6 or t.z > 34:
                continue
            if t.z < best_z:
                best, best_z = t, t.z
        return best

    def poll(self, dt: float) -> InputState:
        fire = False
        self._cool -= dt
        tgt = self._pick()
        if tgt is not None:
            sx, sy, sr = self.world.project(tgt)
            k = 1.0 - math.exp(-dt * 11.0)
            self.x += (sx - self.x) * k
            self.y += (sy - self.y) * k
            if self._cool <= 0 and math.hypot(sx - self.x, sy - self.y) < max(sr, 18) * 0.7:
                if self.rng.random() < self.skill:
                    fire = True
                self._cool = 0.28
        else:
            k = 1.0 - math.exp(-dt * 3.0)
            self.x += (C.CX - self.x) * k
            self.y += (C.CY - self.y) * k
        self._fist = fire
        return InputState(x=self.x, y=self.y, present=True, fist=self._fist, fire=fire,
                          fire_x=self.x, fire_y=self.y)
