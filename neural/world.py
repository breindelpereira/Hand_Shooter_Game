"""Pseudo-3D world: targets live at a depth z and rush toward the player.

Pure game logic (no pygame) so it can be unit tested and driven by a bot.
Screen position = vanishing point + (x, y) * FOCAL / z.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from . import config as C

# kind -> parameters.  speed is a multiplier on the world speed.
KINDS = {
    "standard":  dict(r=0.38, speed=1.00, hp=1, pts=100, color=C.CYAN),
    "fast":      dict(r=0.32, speed=1.55, hp=1, pts=150, color=C.ORANGE, wob=0.25),
    "small":     dict(r=0.20, speed=1.10, hp=1, pts=200, color=C.MAGENTA, wob=0.30),
    "splitter":  dict(r=0.44, speed=0.90, hp=1, pts=150, color=C.GREEN),
    "mini":      dict(r=0.21, speed=1.05, hp=1, pts=60,  color=C.GREEN),
    "shielded":  dict(r=0.46, speed=0.85, hp=2, pts=250, color=C.WHITE),
    "mine":      dict(r=0.40, speed=0.90, hp=1, pts=0,   color=C.RED, hazard=True),
    "timewarp":  dict(r=0.38, speed=0.80, hp=1, pts=100, color=C.GOLD, powerup=True),
    "overcharge": dict(r=0.38, speed=0.80, hp=1, pts=100, color=C.PURPLE, powerup=True),
    "repair":    dict(r=0.38, speed=0.80, hp=1, pts=100, color=C.BLUE, powerup=True),
    "node":      dict(r=0.30, speed=0.0, hp=1, pts=300, color=C.GOLD),
    "boss":      dict(r=1.30, speed=0.26, hp=1, pts=0, color=C.RED),
}

POWERUPS = ("timewarp", "overcharge", "repair")


class Target:
    __slots__ = ("kind", "x", "y", "z", "x0", "y0", "vx", "hp", "r", "alive", "age", "wob_amp",
                 "wob_freq", "phase", "parent", "orbit_a", "orbit_r", "orbit_w", "speed",
                 "hazard", "powerup", "pts", "color", "flash", "children", "mini",
                 "sw_x", "sw_y", "sw_end", "ring_c", "ring_r", "ring_a", "ring_w")

    def __init__(self, kind: str, x: float, y: float, z: float):
        p = KINDS[kind]
        self.kind = kind
        self.x = self.x0 = x
        self.y = self.y0 = y
        self.z = z
        self.sw_x = self.sw_y = 0.0     # "swoop": start offset that decays to the lane by sw_end
        self.sw_end = 13.0
        self.ring_c = None              # (cx, cy) when part of a spinning ring formation
        self.ring_r = self.ring_a = self.ring_w = 0.0
        self.vx = 0.0
        self.hp = p["hp"]
        self.r = p["r"]
        self.speed = p["speed"]
        self.pts = p["pts"]
        self.color = p["color"]
        self.hazard = p.get("hazard", False)
        self.powerup = p.get("powerup", False)
        self.alive = True
        self.age = 0.0
        self.wob_amp = 0.0
        self.wob_freq = 0.0
        self.phase = 0.0
        self.parent = None
        self.orbit_a = self.orbit_r = self.orbit_w = 0.0
        self.flash = 0.0
        self.children: list = []
        self.mini = False            # boss variant flag

    @property
    def counts_as_leak(self) -> bool:
        return not (self.hazard or self.powerup)


@dataclass
class Hit:
    target: Target
    killed: bool
    perfect: bool
    sx: float
    sy: float
    sr: float
    boss_down: bool = False
    blast: bool = False


class World:
    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.targets: list[Target] = []
        self.base_speed = 1.0         # set by the mode (difficulty)
        self.time_scale = 1.0         # set by time-warp
        self.scroll = 0.0             # tunnel scroll distance (for visuals)
        self.fair_spawns = False      # True: lane choice ignores existing targets (identical Duel turns)

    # ---- helpers ---------------------------------------------------------
    @staticmethod
    def project(t: Target):
        k = C.FOCAL / max(t.z, 0.2)
        return C.CX + t.x * k, C.CY + t.y * k, t.r * k

    @property
    def speed(self) -> float:
        return self.base_speed * self.time_scale

    def active_count(self, include_powerups=False) -> int:
        return sum(1 for t in self.targets
                   if t.alive and t.kind not in ("node",) and
                   (include_powerups or not t.powerup))

    def pick_lane(self, tries: int = 7):
        """Random (x, y) that is far from targets still near the spawn depth."""
        best, best_d = (0.0, 0.0), -1.0
        near = [] if self.fair_spawns else \
            [(t.x, t.y) for t in self.targets if t.alive and t.z > C.Z_FAR * 0.55]
        for _ in range(tries):
            x = self.rng.uniform(-C.X_RANGE, C.X_RANGE)
            y = self.rng.uniform(-C.Y_RANGE, C.Y_RANGE)
            d = min((math.hypot(x - a, (y - b) * 1.4) for a, b in near), default=9.0)
            if d > best_d:
                best, best_d = (x, y), d
        return best

    # ---- spawning --------------------------------------------------------
    def spawn(self, kind: str, x: float | None = None, y: float | None = None,
              z: float = C.Z_FAR, swoop=None) -> Target:
        if x is None or y is None:
            x, y = self.pick_lane()
        t = Target(kind, x, y, z)
        if swoop:
            t.sw_x, t.sw_y = swoop
        wob = KINDS[kind].get("wob", 0.0)
        if wob:
            t.wob_amp = wob * self.rng.uniform(0.7, 1.3)
            t.wob_freq = self.rng.uniform(1.6, 3.0)
            t.phase = self.rng.uniform(0, math.tau)
        self.targets.append(t)
        return t

    def random_swoop(self):
        """A start offset that makes a target curve in from the side / top / bottom."""
        return (self.rng.choice((-1, 1)) * self.rng.uniform(14, 24), self.rng.uniform(-9, 7))

    def spawn_formation(self, kinds, shape: str, x=None, y=None, z: float = C.Z_FAR):
        """A group of targets: 'ring' (spins as it approaches), 'arc' or 'vee'."""
        n = len(kinds)
        if x is None or y is None:
            x, y = self.pick_lane()
        x = max(-C.X_RANGE * 0.6, min(C.X_RANGE * 0.6, x))
        y = max(-C.Y_RANGE * 0.5, min(C.Y_RANGE * 0.5, y))
        spin = self.rng.choice((-1, 1)) * self.rng.uniform(0.7, 1.2)
        out = []
        for i, kind in enumerate(kinds):
            if shape == "ring":
                t = self.spawn(kind, x, y, z)
                t.ring_c = (x, y)
                t.ring_r = 0.85
                t.ring_a = i * math.tau / n
                t.ring_w = spin
            elif shape == "arc":
                a = (i / max(1, n - 1) - 0.5) * 2.4
                t = self.spawn(kind, x + 1.15 * math.sin(a), y + 0.45 - 0.55 * math.cos(a), z + i * 0.9)
            else:                                   # vee
                k = i - (n - 1) / 2
                t = self.spawn(kind, x + k * 0.62, y + abs(k) * 0.34, z + abs(k) * 3.2)
            out.append(t)
        return out

    def spawn_boss(self, nodes: int = 3, mini: bool = False) -> Target:
        b = Target("boss", self.rng.uniform(-0.6, 0.6), self.rng.uniform(-0.3, 0.3), 40.0)
        b.mini = mini
        if mini:
            b.r *= 0.75
        b.wob_amp = 0.9
        b.wob_freq = 0.55
        b.phase = self.rng.uniform(0, math.tau)
        self.targets.append(b)
        for i in range(nodes):
            n = Target("node", b.x, b.y, b.z)
            n.parent = b
            n.orbit_a = i * math.tau / nodes
            n.orbit_r = b.r * 1.55
            n.orbit_w = 1.1 if i % 2 == 0 else -1.1
            n.hp = 1
            b.children.append(n)
            self.targets.append(n)
        return b

    # ---- simulation ------------------------------------------------------
    def update(self, dt: float):
        """Advance the world. Returns [('leak'|'pass'|'boss_leak', target), ...]."""
        events = []
        spd = self.speed
        self.scroll += C.BASE_SPEED * spd * dt
        for t in self.targets:
            if not t.alive:
                continue
            t.age += dt
            if t.flash > 0:
                t.flash = max(0.0, t.flash - dt)
            if t.kind == "node":
                p = t.parent
                if p is None or not p.alive:
                    t.alive = False
                    continue
                t.orbit_a += t.orbit_w * dt
                t.x = p.x + math.cos(t.orbit_a) * t.orbit_r
                t.y = p.y + math.sin(t.orbit_a) * t.orbit_r * 0.8
                t.z = p.z
                continue
            t.z -= C.BASE_SPEED * t.speed * spd * dt
            t.x0 += t.vx * dt
            if t.ring_c is not None:                      # spinning ring formation
                a = t.ring_a + t.ring_w * t.age
                t.x0 = t.ring_c[0] + t.ring_r * math.cos(a)
                t.y0 = t.ring_c[1] + t.ring_r * math.sin(a) * 0.8
            s = 0.0
            if t.sw_x or t.sw_y:                          # decaying swoop offset
                s = (t.z - t.sw_end) / (C.Z_FAR - t.sw_end)
                s = 0.0 if s <= 0 else min(1.0, s) ** 1.4
            t.x = t.x0 + (t.wob_amp * math.sin(t.age * t.wob_freq + t.phase)
                          if t.wob_amp else 0.0) + t.sw_x * s
            t.y = t.y0 + t.sw_y * s
            if t.kind == "boss":
                if t.z <= C.BOSS_PASS_Z:
                    t.alive = False
                    for n in t.children:
                        n.alive = False
                    events.append(("boss_leak", t))
            elif t.z <= C.Z_PASS:
                t.alive = False
                events.append(("leak" if t.counts_as_leak else "pass", t))
        self.targets = [t for t in self.targets if t.alive]
        return events

    # ---- shooting --------------------------------------------------------
    def _hit_radius(self, sr: float) -> float:
        return max(sr * C.HIT_ASSIST + 4.0, C.HIT_MIN_RADIUS)

    def target_at(self, px: float, py: float):
        """The shootable target the point is aimed at, or None.  When several overlap, the one
        you are pointing at most precisely (smallest distance relative to its hit radius)
        wins; exact ties go to the one closest to the player."""
        best, best_key = None, None
        for t in self.targets:
            if not t.alive or t.kind == "boss":
                continue
            sx, sy, sr = self.project(t)
            hr = self._hit_radius(sr)
            d = math.hypot(px - sx, py - sy)
            if d > hr:
                continue
            key = (round(d / hr, 3), t.z)
            if best_key is None or key < best_key:
                best, best_key = t, key
        return best

    def lock_candidate(self, px: float, py: float, current=None):
        """Auto-lock (easy mode): the non-hazard target the point is closest to, within a
        capture zone sized from the target itself (``AUTO_CAPTURE_*``).  A held lock is a
        little sticky (``AUTO_STICK``).  Firing at the returned target's centre always hits it."""
        scored = []
        for t in self.targets:
            if not t.alive or t.kind == "boss" or t.hazard:
                continue
            sx, sy, sr = self.project(t)
            cap = max(sr * C.AUTO_CAPTURE_SCALE + C.AUTO_CAPTURE_PAD, C.AUTO_CAPTURE_PX)
            d = math.hypot(px - sx, py - sy)
            if d > cap * (C.AUTO_STICK if t is current else 1.0):
                continue
            s = d / cap - (0.45 if t is current else 0.0) + t.z * 0.002   # sticky, then urgent
            scored.append((s, t, sx, sy))
        scored.sort(key=lambda it: it[0])
        for _s, t, sx, sy in scored:
            if self.target_at(sx, sy) is t:          # nothing else would intercept the shot
                return t
        return None

    def _damage(self, t: Target, blast: bool = False, sx=0.0, sy=0.0, sr=0.0, perfect=False):
        t.hp -= 1
        t.flash = 0.12
        killed = t.hp <= 0
        boss_down = False
        if killed:
            t.alive = False
            if t.kind == "splitter":
                for sgn in (-1, 1):
                    m = self.spawn("mini", t.x, t.y, max(t.z, C.Z_PASS + 1))
                    m.vx = 0.9 * sgn
                    m.wob_amp = 0.0
            if t.kind == "node" and t.parent is not None:
                if not any(n.alive and n is not t for n in t.parent.children):
                    t.parent.alive = False
                    boss_down = True
        return Hit(t, killed, perfect, sx, sy, sr, boss_down=boss_down, blast=blast)

    def fire(self, px: float, py: float, spread: float = 0.0):
        """Hitscan at (px, py). With spread > 0 fires three rays (centre, left, right).
        Returns a list of Hit; empty means a miss."""
        hits: list[Hit] = []
        offsets = (0.0,) if not spread else (0.0, -spread, spread)
        done = set()
        for ox in offsets:
            t = self.target_at(px + ox, py)
            if t is None or id(t) in done:
                continue
            done.add(id(t))
            sx, sy, sr = self.project(t)
            perfect = math.hypot(px + ox - sx, py - sy) <= sr * C.PERFECT_FRACTION
            hits.append(self._damage(t, False, sx, sy, sr, perfect))
        self.targets = [t for t in self.targets if t.alive]
        return hits

    def blast(self, px: float, py: float, radius: float = C.BLAST_RADIUS):
        """Charged shot: destroys everything in a ring, mines included (safely)."""
        hits = []
        for t in list(self.targets):
            if not t.alive or t.kind == "boss":
                continue
            sx, sy, sr = self.project(t)
            if math.hypot(px - sx, py - sy) <= radius + sr:
                t.hp = 1
                h = self._damage(t, True, sx, sy, sr, False)
                hits.append(h)
        self.targets = [t for t in self.targets if t.alive]
        return hits

    def clear_all(self):
        """Pop everything (end of level / end of run)."""
        hits = []
        for t in list(self.targets):
            if t.alive and t.kind != "node":
                sx, sy, sr = self.project(t)
                t.alive = False
                hits.append(Hit(t, True, False, sx, sy, sr, blast=True))
        for t in self.targets:
            t.alive = False
        self.targets = []
        return hits
