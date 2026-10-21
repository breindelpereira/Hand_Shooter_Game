"""Session files (written by HandInput.stop_recording) and replay of them.

load_session()  -> (meta dict, list of row dicts)
ReplayInput     -> plays a recorded session back as game input (demos, regression tests)
"""
from __future__ import annotations

import csv

from . import config as C
from .inputs import InputProvider, InputState


def load_session(path):
    meta: dict = {}
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        header_lines = []
        pos = f.tell()
        line = f.readline()
        while line.startswith("#"):
            header_lines.append(line[1:].strip())
            pos = f.tell()
            line = f.readline()
        f.seek(pos)
        for part in " ".join(header_lines).split():
            if "=" in part:
                k, v = part.split("=", 1)
                try:
                    meta[k] = float(v)
                except ValueError:
                    meta[k] = v
        for r in csv.DictReader(f):
            def num(key):
                v = r.get(key, "")
                return float(v) if v not in ("", None) else None
            rows.append(dict(t=float(r["t"]), present=int(r["present"]), x=float(r["x"]),
                             y=float(r["y"]), fist=num("fist"), pinch=num("pinch"),
                             closed=int(r["closed"]), fired=int(r["fired"])))
    return meta, rows


class ReplayInput(InputProvider):
    """Plays a recorded hand session back as input. Loops when it reaches the end."""

    name = "replay"
    has_camera = False

    def __init__(self, path, loop: bool = True, speed: float = 1.0):
        self.meta, self.rows = load_session(path)
        if not self.rows:
            raise ValueError(f"empty session: {path}")
        self.loop = loop
        self.speed = speed
        self.t = 0.0
        self.i = 0
        self.duration = self.rows[-1]["t"]
        self._x, self._y = C.W / 2.0, C.H / 2.0

    def poll(self, dt: float) -> InputState:
        self.t += dt * self.speed
        fired_pos = None
        fire = False
        cur = self.rows[min(self.i, len(self.rows) - 1)]
        while self.i < len(self.rows) and self.rows[self.i]["t"] <= self.t:
            cur = self.rows[self.i]
            if cur["fired"]:
                fire = True
                fired_pos = (cur["x"] * C.W, cur["y"] * C.H)
            self.i += 1
        if self.i >= len(self.rows) and self.loop:
            self.t = 0.0
            self.i = 0
        present = bool(cur["present"])
        if present:
            self._x, self._y = cur["x"] * C.W, cur["y"] * C.H
        fx, fy = fired_pos if fired_pos else (self._x, self._y)
        ratio = cur["fist"] if cur["fist"] is not None else 1.8
        return InputState(x=self._x, y=self._y, present=present, fist=bool(cur["closed"]) and present,
                          fire=fire and present, fire_x=fx, fire_y=fy, ratio=ratio)
