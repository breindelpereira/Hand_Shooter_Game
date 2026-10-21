"""Game modes. A mode owns the rules: pacing, spawning, penalties and when the run ends.

Both modes drive the same World; the scene reads ``mode.events`` to trigger feedback.
"""
from __future__ import annotations

import math
import random

from . import config as C
from .world import POWERUPS, World
from .scoring import Scoring


def lerp(a, b, t):
    return a + (b - a) * t


def clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def smoothstep(e0, e1, x):
    t = clamp((x - e0) / (e1 - e0))
    return t * t * (3 - 2 * t)


class Mode:
    key = "?"
    title = "?"

    def __init__(self, world: World, scoring: Scoring, rng: random.Random | None = None):
        self.world = world
        self.scoring = scoring
        self.rng = rng or random.Random()
        self.phase = "countdown"        # countdown | play | intro | clear | over
        self.events: list[tuple] = []
        self.lives: int | None = None
        self.max_lives = 0
        self.emp = 0.0                  # seconds of EMP interference remaining
        self.emp_warn = 0.0

    # --- interface ---
    def update(self, dt: float, present: bool): raise NotImplementedError
    def on_leak(self, t): raise NotImplementedError
    def on_mine(self, t): raise NotImplementedError
    def on_boss_leak(self, t): raise NotImplementedError

    def pop_events(self):
        ev, self.events = self.events, []
        return ev

    @property
    def over(self) -> bool:
        return self.phase == "over"

    def _weighted(self, weights: dict) -> str:
        kinds = list(weights)
        return self.rng.choices(kinds, [weights[k] for k in kinds])[0]

    def _emp_tick(self, dt):
        if self.emp_warn > 0:
            self.emp_warn -= dt
            if self.emp_warn <= 0:
                self.emp = 1.6
                self.events.append(("emp_on",))
        elif self.emp > 0:
            self.emp -= dt


class TimedMode(Mode):
    """Score attack: 5 s get-ready, slow start, continuous ramp until the clock ends."""

    key = "timed"
    title = "SCORE ATTACK"

    def __init__(self, world, scoring, rng=None, duration: float = C.TIMED_DURATION,
                 lives: int | None = C.TIMED_LIVES):
        super().__init__(world, scoring, rng)
        self.duration = duration
        self.cd = C.TIMED_COUNTDOWN
        self._last_tick = int(math.ceil(self.cd)) + 1
        self.t = 0.0
        self.spawn_timer = 1.0
        self.lives = lives
        self.max_lives = lives or 0
        self.powerups_at = [duration * 0.22, duration * 0.48, duration * 0.74]
        self.boss_at = duration * 0.55
        self.emp_at = [duration * 0.70, duration * 0.90]
        self.world.base_speed = 0.5
        self.level = 0
        # --- adaptive difficulty: how hard it gets follows how well the player is doing ---
        self.heat = 0.30                 # 0..1, performance: up on kills/combos, down on mistakes
        self._int = 0.15                 # smoothed intensity actually used for pacing
        self._k0 = self._l0 = self._m0 = 0
        self._idle = 0.0

    @property
    def intensity(self) -> float:
        """0..1 difficulty. A struggling player stays gentle however long the clock has run;
        a player doing well is pushed up toward 1 (and earns a points bonus for it)."""
        return self._int

    def _track_performance(self, dt: float):
        sc = self.scoring
        dk, dl, dm = sc.kills - self._k0, sc.leaks - self._l0, sc.mines_hit - self._m0
        self._k0, self._l0, self._m0 = sc.kills, sc.leaks, sc.mines_hit
        if dk > 0:
            self.heat += dk * (0.028 + 0.0012 * min(sc.combo, 30))
            self._idle = 0.0
        else:
            self._idle += dt
        if dl > 0:
            self.heat -= 0.11 * dl
        if dm > 0:
            self.heat -= 0.08 * dm
        if self._idle > 3.0:                     # nothing being hit: ease off
            self.heat -= 0.025 * dt
        self.heat = clamp(self.heat)
        # time still matters a little (late game can go higher), performance matters most
        target = clamp(0.10 + 0.90 * self.heat * (0.45 + 0.55 * self.d))
        self._int += (target - self._int) * min(1.0, dt * 0.8)
        sc.bonus = 1.0 + 0.6 * self._int         # harder play pays more

    # progress 0..1
    @property
    def d(self) -> float:
        return clamp(self.t / self.duration)

    @property
    def time_left(self) -> float:
        return max(0.0, self.duration - self.t)

    def update(self, dt: float, present: bool):
        if self.phase == "countdown":
            if present:
                self.cd -= dt
                n = int(math.ceil(self.cd))
                if n < self._last_tick and n >= 1:
                    self._last_tick = n
                    self.events.append(("tick", n))
                if self.cd <= 0:
                    self.phase = "play"
                    self.events.append(("go",))
            return
        if self.phase != "play":
            return

        self.t += dt
        self._track_performance(dt)
        d = self.intensity                       # pacing follows performance, not just the clock
        self.world.base_speed = lerp(0.55, 1.75, d ** 1.1)
        self._emp_tick(dt)

        # scheduled one-offs
        if self.powerups_at and self.t >= self.powerups_at[0]:
            self.powerups_at.pop(0)
            self.world.spawn(self.rng.choice(POWERUPS))
        if self.boss_at is not None and self.t >= self.boss_at:
            self.boss_at = None
            self.world.spawn_boss(nodes=2, mini=True)
            self.events.append(("boss_warning",))
        if self.emp_at and self.t >= self.emp_at[0]:
            self.emp_at.pop(0)
            if d > 0.40:                         # only throw interference at a player who is coping
                self.emp_warn = 1.0
                self.events.append(("emp_warn",))

        # regular spawning
        self.spawn_timer -= dt
        if self.spawn_timer <= 0:
            self.spawn_timer = lerp(2.0, 0.50, smoothstep(0.0, 1.0, d) ** 0.9) * \
                self.rng.uniform(0.85, 1.15)
            n = self._spawn_wave(d)
            self.spawn_timer *= 1.0 + 0.16 * (n - 1)        # bigger groups buy breathing room

        if self.t >= self.duration:
            self.phase = "over"
            self.events.append(("finished",))

    def _weights(self, d):
        w = {"standard": 1.0}
        w["fast"] = 0.7 * smoothstep(0.12, 0.40, d)
        w["small"] = 0.6 * smoothstep(0.20, 0.50, d)
        w["splitter"] = 0.45 * smoothstep(0.30, 0.55, d)
        w["shielded"] = 0.40 * smoothstep(0.38, 0.60, d)
        w["mine"] = 0.40 * smoothstep(0.26, 0.50, d) + 0.15 * d
        return {k: v for k, v in w.items() if v > 0.01}

    def _spawn_wave(self, d) -> int:
        """Spawn one thing (single / line / formation). Returns how many targets it was."""
        rng = self.rng
        if d > 0.28 and rng.random() < 0.08 + 0.22 * d:
            shape = rng.choice(("ring", "arc", "vee"))
            n = {"ring": 6, "arc": 4, "vee": 5}[shape]
            pool = ["standard", "small", "fast", "standard"] if d > 0.5 else ["standard", "small"]
            kind = rng.choice(pool)
            self.world.spawn_formation([kind] * n, shape)
            return n
        kind = self._weighted(self._weights(d))
        n = 1
        if d > 0.35 and kind in ("standard", "fast", "small") and rng.random() < 0.18 + 0.2 * d:
            n = rng.randint(2, 3)
        x, y = self.world.pick_lane()
        swoop = self.world.random_swoop() if rng.random() < 0.12 + 0.5 * d else None
        for i in range(n):
            # a short line of targets: same lane, staggered depth
            self.world.spawn(kind, x + (i - (n - 1) / 2) * 0.55, y, C.Z_FAR + i * 4.0, swoop=swoop)
        return n

    # penalties -- timed mode costs points, not (by default) lives
    def on_leak(self, t):
        self.scoring.reset_combo()
        self.scoring.leaks += 1
        self.scoring.add_points(-C.TIMED_LEAK_PENALTY)
        self._lose_life(1)
        return f"-{C.TIMED_LEAK_PENALTY}"

    def on_mine(self, t):
        self.scoring.reset_combo()
        self.scoring.mines_hit += 1
        self.scoring.add_points(-C.TIMED_MINE_PENALTY)
        self._lose_life(1)
        return f"-{C.TIMED_MINE_PENALTY}"

    def on_boss_leak(self, t):
        self.scoring.reset_combo()
        self.scoring.leaks += 1
        self.scoring.add_points(-300)
        self._lose_life(1)
        return "-300"

    def _lose_life(self, n):
        if self.lives is None:
            return
        self.lives = max(0, self.lives - n)
        if self.lives == 0 and self.phase != "over":
            self.phase = "over"
            self.events.append(("game_over",))


def level_spec(n: int) -> dict:
    """Everything that defines endless level n."""
    quota = min(6 + 3 * (n - 1), 42)
    speed = min(0.80 * (1 + 0.07 * (n - 1)), 1.95)
    gap = max(0.55, 1.9 - 0.09 * (n - 1))
    cap = min(3 + n // 2, 9)
    w = {"standard": 1.0}
    if n >= 3:
        w["fast"] = 0.55
    if n >= 4:
        w["small"] = 0.55
    if n >= 6:
        w["mine"] = 0.38
    if n >= 7:
        w["splitter"] = 0.45
    if n >= 8:
        w["shielded"] = 0.45
    boss = None
    if n % 10 == 0:
        boss = "boss"
    elif n % 5 == 0:
        boss = "mini"
    notes = {
        1: "SHOOT THE TARGETS", 2: "MORE TARGETS", 3: "FAST TARGETS", 4: "SMALL TARGETS",
        5: "MINI-BOSS", 6: "MINES - DON'T SHOOT", 7: "SPLITTERS", 8: "SHIELDED TARGETS",
        9: "POWER-UPS", 10: "BOSS: THE CORE",
    }
    return dict(quota=quota, speed=speed, gap=gap, cap=cap, weights=w, boss=boss,
                note=notes.get(n, "FASTER. DENSER."))


class EndlessMode(Mode):
    """Levels of growing difficulty, 3 lives, short breaks between levels."""

    key = "endless"
    title = "SURVIVAL"

    def __init__(self, world, scoring, rng=None, lives: int = C.START_LIVES):
        super().__init__(world, scoring, rng)
        self.lives = lives
        self.max_lives = C.MAX_LIVES
        self.level = 0
        self.timer = 0.0
        self.spec: dict = {}
        self.queue: list[str] = []
        self.spawn_timer = 0.0
        self.boss_spawned = False
        self.boss_done = True
        self.level_lives_lost = 0
        self.level_shots0 = 0
        self.level_hits0 = 0
        self.cleared_info: dict = {}
        self._start_level(1)
        self.events.clear()              # the intro card is shown after the countdown
        self.phase = "countdown"
        self.cd = 2.0
        self._last_tick = 3
        self.world.base_speed = 0.8

    # --- level management ---
    def _start_level(self, n: int):
        self.level = n
        self.spec = level_spec(n)
        q = [self._weighted(self.spec["weights"]) for _ in range(self.spec["quota"])]
        if n >= 2:
            # one power-up in the middle of the level; repair is favoured when hurt
            w = {"timewarp": 1.0, "overcharge": 1.0,
                 "repair": 2.0 if self.lives < 3 else 0.4}
            q.insert(len(q) // 2, self._weighted(w))
        self.queue = q
        self.boss_spawned = False
        self.boss_done = self.spec["boss"] is None
        self.spawn_timer = 1.0
        self.level_lives_lost = 0
        self.level_shots0, self.level_hits0 = self.scoring.shots, self.scoring.hits
        self.world.base_speed = self.spec["speed"]
        self.phase = "intro"
        self.timer = C.LEVEL_INTRO_TIME
        self.events.append(("level_intro", n, self.spec["note"]))

    def update(self, dt: float, present: bool):
        if self.phase == "countdown":
            if present:
                self.cd -= dt
                n = int(math.ceil(self.cd))
                if n < self._last_tick and n >= 1:
                    self._last_tick = n
                    self.events.append(("tick", n))
                if self.cd <= 0:
                    self.phase = "intro"
                    self.timer = C.LEVEL_INTRO_TIME
                    self.events.append(("level_intro", self.level, self.spec["note"]))
            return
        if self.phase == "intro":
            self.timer -= dt
            if self.timer <= 0:
                self.phase = "play"
                self.events.append(("go",))
            return
        if self.phase == "clear":
            self.timer -= dt
            if self.timer <= 0:
                self._start_level(self.level + 1)
            return
        if self.phase != "play":
            return

        # --- spawning ---
        self.spawn_timer -= dt
        alive = self.world.active_count()
        if self.queue and self.spawn_timer <= 0 and alive < self.spec["cap"]:
            kind = self.queue.pop(0)
            lvl = self.level
            plain = ("standard", "small", "fast")
            group = (lvl >= 3 and kind in plain and len(self.queue) >= 3
                     and all(k in plain for k in self.queue[:3])
                     and self.rng.random() < 0.10 + 0.02 * min(lvl, 14))
            if group:
                shape = self.rng.choice(("arc", "vee", "ring") if lvl >= 5 else ("arc", "vee"))
                n = min({"ring": 6, "arc": 4, "vee": 5}[shape], len(self.queue) + 1)
                kinds = [kind] + [self.queue.pop(0) for _ in range(n - 1)]
                if not all(k in plain for k in kinds):          # never wrap a mine in a ring
                    kinds = [k if k in plain else "standard" for k in kinds]
                self.world.spawn_formation(kinds, shape)
                self.spawn_timer = self.spec["gap"] * (1.0 + 0.2 * n) * self.rng.uniform(0.9, 1.1)
            else:
                swoop = (self.world.random_swoop()
                         if lvl >= 2 and self.rng.random() < min(0.55, 0.12 + 0.04 * lvl) else None)
                self.world.spawn(kind, swoop=swoop)
                self.spawn_timer = self.spec["gap"] * self.rng.uniform(0.8, 1.2)
        if not self.queue and not self.boss_spawned and self.spec["boss"] and alive == 0:
            self.boss_spawned = True
            self.boss_done = False
            big = self.spec["boss"] == "boss"
            self.world.spawn_boss(nodes=3 if big else 2, mini=not big)
            self.events.append(("boss_warning",))

        # --- level resolved? ---
        if self.boss_spawned and not self.boss_done:
            if not any(t.kind == "boss" and t.alive for t in self.world.targets):
                self.boss_done = True
        if (not self.queue and self.boss_done and self.world.active_count() == 0
                and self.phase == "play"):
            self._level_clear()

    def _level_clear(self):
        flawless = self.level_lives_lost == 0
        shots = self.scoring.shots - self.level_shots0
        hits = self.scoring.hits - self.level_hits0
        acc = hits / shots if shots else 1.0
        bonus = 250 * self.level + (500 if flawless else 0) + int(300 * acc)
        self.scoring.add_points(bonus)
        gained_life = False
        if flawless and self.lives < self.max_lives:
            self.lives += 1
            gained_life = True
        self.cleared_info = dict(level=self.level, bonus=bonus, flawless=flawless,
                                 accuracy=acc, life=gained_life)
        self.phase = "clear"
        self.timer = C.LEVEL_CLEAR_TIME
        self.events.append(("level_clear", dict(self.cleared_info)))

    # --- penalties ---
    def _lose_life(self, n=1):
        self.scoring.reset_combo()
        self.lives = max(0, self.lives - n)
        self.level_lives_lost += n
        if self.lives == 0:
            self.phase = "over"
            self.events.append(("game_over",))

    def on_leak(self, t):
        self.scoring.leaks += 1
        self._lose_life(1)
        return "-1 LIFE"

    def on_mine(self, t):
        self.scoring.mines_hit += 1
        self._lose_life(1)
        return "-1 LIFE"

    def on_boss_leak(self, t):
        self.scoring.leaks += 1
        self._lose_life(2)
        return "-2 LIVES"
