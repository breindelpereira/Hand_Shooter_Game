"""Two-player modes.

  * VersusScene  - both players at once on one screen; highest score wins.
  * VersusScene(coop=True) - both players at once, one shared score / combo.

Everything here is additive: switch it off with config.TWO_PLAYER = False (the choose screen
then shows no PLAYERS option) or go back to the `baseline-1p` git tag.
"""
from __future__ import annotations

import math
import random

import pygame

from . import config as C
from . import scoring as S
from .fx import FX, draw_text, mix, scale_color
from .modes import TimedMode
from .render import (draw_bar, draw_boss_tethers, draw_hud_panel, draw_reticle, draw_target)
from .scenes import MenuScene, PlayScene, ResultsScene, Scene
from .scoring import Scoring
from .ui import BigButton, Button
from .world import World

P_COLORS = (C.CYAN, C.MAGENTA)
P_NAMES = ("P1", "P2")


# =============================================================== Live 2-player
class PlayerSlot:
    """Per-player state for a live two-player game."""

    def __init__(self, idx: int):
        self.idx = idx
        self.color = P_COLORS[idx]
        self.label = P_NAMES[idx]
        self.sc = Scoring()
        self.rx, self.ry = C.W * (0.3 if idx == 0 else 0.7), C.CY
        self.raw_x, self.raw_y = self.rx, self.ry
        self.present = False
        self.lost = 0.0
        self.fist = False
        self.held = 0.0
        self.charge_snd = False
        self.cool = 0.0
        self.overcharge = 0.0
        self.lock_t = 0.0
        self.lock_target = None
        self.locked = None
        self.pulse = 0.0
        self.disp = 0.0


class VersusScene(Scene):
    """Both players at once. Versus: highest score wins. Co-op: one shared score and combo."""

    show_guide = False
    pip_scale = 0.75

    def __init__(self, app, coop: bool = False):
        super().__init__(app)
        self.coop = coop
        self.autofire = bool(app.cfg.autofire)
        self.rng = random.Random()
        self.jit = random.Random()
        self.world = World(self.rng)
        self.team = Scoring()                 # pacing (heat / bonus) for both; the score itself in co-op
        self.mode = TimedMode(self.world, self.team, self.rng, duration=app.cfg.duration,
                              lives=None, adaptive=True)
        self.fx = FX(random.Random())
        self.p = [PlayerSlot(0), PlayerSlot(1)]
        self.hitstop = 0.0
        self.warp = 0.0
        self.paused = False
        self.end_timer = None
        self.jx = self.jy = 0.0
        self.wait_t = 0.0
        self.frozen = False
        self.table = "coop" if coop else "versus"
        self.menu_btn = Button((C.CX - 64, C.H - 64, 128, 42), "MENU", "menu", C.GREY, size=22, dwell=999.0)
        self.pause_buttons = [
            Button((C.CX - 190, 330, 380, 64), "RESUME", None, C.CYAN),
            Button((C.CX - 190, 410, 380, 64), "QUIT TO MENU", None, C.ORANGE),
        ]

    # ---- lifecycle
    def enter(self):
        a = self.app
        a.set_players(2)
        a.audio.sfx_muted = False
        a.audio.music_start()

    def leave(self):
        self.app.set_players(1)

    def on_key(self, key):
        if key in (pygame.K_ESCAPE, pygame.K_p) and self.end_timer is None:
            self.paused = not self.paused
            self.age = 0.0

    # ---- helpers
    def score_of(self, p: PlayerSlot) -> int:
        return self.team.score if self.coop else p.sc.score

    # ---- update
    def update(self, dt, inp1):
        self.age += dt
        a, w, fx, mode = self.app, self.world, self.fx, self.mode
        ins = [inp1, a.inp2]

        if self.paused:
            for p, inp in zip(self.p, ins):                  # keep both pointers alive while paused
                p.rx, p.ry, p.present = inp.x, inp.y, inp.present
            for i, b in enumerate(self.pause_buttons):
                if any(b.update(dt, inp, locked=self.age < 0.2) for inp in ins):
                    a.audio.play_ui("select")
                    if i == 0:
                        self.paused = False
                    else:
                        a.goto(MenuScene(a))
                        return
            return

        needs_hand = a.input.has_camera
        for p, inp in zip(self.p, ins):
            p.present = inp.present
            p.lost = 0.0 if inp.present else p.lost + dt
            p.raw_x, p.raw_y = inp.x, inp.y
        self.frozen = needs_hand and all(p.lost > C.HAND_LOST_GRACE for p in self.p)

        # shared EMP interference
        emp = mode.emp > 0
        if emp:
            self.jx += (self.jit.uniform(-45, 45) - self.jx) * min(1.0, dt * 18)
            self.jy += (self.jit.uniform(-45, 45) - self.jy) * min(1.0, dt * 18)
        else:
            self.jx = self.jy = 0.0
        for p, inp in zip(self.p, ins):
            if p.present:
                p.rx = min(max(inp.x + self.jx, 0), C.W)
                p.ry = min(max(inp.y + self.jy, 0), C.H)
            p.pulse = max(0.0, p.pulse - dt * 5)
            p.sc.bonus = self.team.bonus

        if self.frozen:
            fx.update(dt * 0.3)
            return
        if self.hitstop > 0:
            self.hitstop -= dt
            fx.update(dt * 0.15)
            return
        fx.update(dt)

        # shared power-ups
        if self.warp > 0:
            self.warp -= dt
        target_scale = C.TIMEWARP_SCALE if self.warp > 0 else 1.0
        w.time_scale += (target_scale - w.time_scale) * min(1.0, dt * 5)
        for p in self.p:
            p.overcharge = max(0.0, p.overcharge - dt)

        # the mode (countdown waits for BOTH players; after 10 s it starts with whoever is there)
        if self.end_timer is None:
            all_here = all(p.present for p in self.p) or not needs_hand
            if mode.phase == "countdown" and not all_here:
                self.wait_t += dt
            ready = all_here or self.wait_t > 10.0
            mode.update(dt, ready)
            self._mode_events()
        else:
            self.end_timer -= dt
            if self.end_timer <= 0:
                self._finish()
                return

        # menu button (click-only, either player)
        if self.end_timer is None:
            for inp in ins:
                if self.menu_btn.update(dt, inp, locked=self.age < 0.6):
                    a.audio.play_ui("select")
                    self.paused = True
                    self.age = 0.0
                    return
        over_menu = [self.menu_btn.hovered(inp.fire_x, inp.fire_y) for inp in ins]

        # shooting
        for p, inp, om in zip(self.p, ins, over_menu):
            p.cool -= dt
            p.fist = inp.fist and inp.present
            if self.end_timer is None and p.present:
                if self.autofire:
                    self._auto_fire(p, dt)
                elif inp.fire and p.cool <= 0 and not om:
                    p.cool = C.SHOT_COOLDOWN
                    self._shoot(p, inp.fire_x + self.jx, inp.fire_y + self.jy)
                self._charged_shot(p, dt, inp)

        for kind, t in w.update(dt):
            self._world_event(kind, t)
        for p in self.p:
            if not self.autofire:
                p.locked = w.target_at(p.rx, p.ry) if p.present else None
            else:
                p.locked = p.lock_target
            p.disp += (self.score_of(p) - p.disp) * min(1.0, dt * 9)

        a.audio.set_intensity(0.1 + 0.9 * mode.intensity if mode.phase == "play" else 0.1)
        if mode.over and self.end_timer is None:
            self.end_timer = 2.6

    # ---- shooting (credited to the shooter)
    def _shoot(self, p: PlayerSlot, px, py, auto=False):
        a, fx = self.app, self.fx
        spread = C.OVERCHARGE_SPREAD if p.overcharge > 0 else 0
        if auto and spread:
            for ox in (-spread, spread):
                t = self.world.target_at(px + ox, py)
                if t is not None and t.hazard:
                    spread = 0
                    break
        hits = self.world.fire(px, py, spread)
        if self.mode.phase == "play":
            p.sc.shot(bool(hits))
        fx.tracer(C.W * (0.35 if p.idx == 0 else 0.65), C.H + 10, px, py, p.color)
        a.audio.play("shoot", 0.6)
        p.pulse = 1.0
        if not hits:
            fx.burst(px, py, C.GREY, n=6, speed=(30, 140), life=(0.15, 0.3), size=(1, 2))
            return
        for h in hits:
            if auto:
                h.perfect = False
            self._on_hit(h, p)

    def _auto_fire(self, p: PlayerSlot, dt):
        w = self.world
        if self.mode.phase != "play":
            p.lock_t = 0.0
            p.lock_target = None
            return
        cand = w.lock_candidate(p.raw_x, p.raw_y, p.lock_target)
        if cand is not p.lock_target:
            p.lock_target = cand
            p.lock_t = 0.0
        if cand is None:
            return
        sx, sy, _sr = w.project(cand)
        p.lock_t += dt
        prog = min(1.0, p.lock_t / C.AUTO_LOCK_TIME)
        pull = C.AUTO_MAGNET * (0.35 + 0.65 * prog)
        p.rx += (sx - p.rx) * pull
        p.ry += (sy - p.ry) * pull
        if p.lock_t >= C.AUTO_LOCK_TIME and p.cool <= 0:
            p.cool = C.AUTO_FIRE_INTERVAL
            self._shoot(p, sx, sy, auto=True)

    def _charged_shot(self, p: PlayerSlot, dt, inp):
        if inp.fist and inp.present:
            p.held += dt
            if p.held > 0.06 and p.sc.charge > 0 and not p.charge_snd:
                p.charge_snd = True
                self.app.audio.play("charge", 0.5)
        else:
            if p.held >= C.CHARGE_HOLD and p.sc.charge > 0 and self.mode.phase == "play":
                self._release_blast(p)
            p.held = 0.0
            p.charge_snd = False

    def _release_blast(self, p: PlayerSlot):
        a, fx = self.app, self.fx
        p.sc.charge -= 1
        hits = self.world.blast(p.rx, p.ry)
        fx.ring(p.rx, p.ry, p.color, C.BLAST_RADIUS, 0.55, 5)
        fx.add_shake(14)
        a.audio.play("blast", 0.8)
        for h in hits:
            t = h.target
            if t.powerup:
                self._apply_powerup(t, p, h.sx, h.sy)
            elif not t.hazard:
                self._credit_kill(p, t, h.sx, h.sy, False, quiet=True)
            if h.boss_down:
                self._boss_bonus(p, 1500)
            fx.explosion(h.sx, h.sy, t.color, 0.9)
        if hits:
            fx.big_callout(f"{p.label} BLAST x{len(hits)}", p.color, 0.9)

    # ---- scoring
    def _credit_kill(self, p, t, x, y, perfect, quiet=False):
        pts, callout, cell = p.sc.kill(t.kind, t.pts, t.z, perfect)
        tpts, _c, _cell = self.team.kill(t.kind, t.pts, t.z, perfect)
        shown = tpts if self.coop else pts
        if not quiet:
            self.fx.popup(x, y - 26, f"+{shown:,}", p.color if not perfect else C.GOLD, 26 if not perfect else 30)
            if perfect:
                self.fx.popup(x, y - 52, "PERFECT", C.GOLD, 18, life=0.7)
            if callout:
                self.fx.big_callout(f"{p.label} {callout}", p.color)
            if cell:
                self.fx.popup(x, y - 80, "CHARGE CELL", C.ORANGE, 20, life=1.0, rise=30)
            self.app.audio.play_hit(min(9, p.sc.combo // 2))
        return shown

    def _boss_bonus(self, p, amount):
        p.sc.add_points(amount)
        self.team.add_points(amount)
        self.fx.big_callout(f"{p.label} CORE DESTROYED", p.color, 1.4)

    def _on_hit(self, h, p: PlayerSlot):
        a, fx = self.app, self.fx
        t = h.target
        x, y, r = h.sx, h.sy, max(10.0, h.sr)
        if t.hazard and h.killed and not h.blast:                      # shot a mine
            if self.coop:
                txt = self.mode.on_mine(t)                              # the whole team pays
            else:
                p.sc.add_points(-C.TIMED_MINE_PENALTY)                  # only the shooter pays
                p.sc.reset_combo()
                self.team.mines_hit += 1
                txt = f"-{C.TIMED_MINE_PENALTY}"
            p.sc.mines_hit += 1
            fx.explosion(x, y, C.RED, 1.4, big=True)
            fx.popup(x, y - 20, txt, p.color, 34)
            fx.add_shake(18)
            a.audio.play("mine")
            self.hitstop = 0.08
            return
        if not h.killed:
            fx.ring(x, y, C.WHITE, r * 1.6, 0.3, 3)
            a.audio.play("shield")
            return
        if t.hazard:
            fx.explosion(x, y, C.RED, 1.0)
            return
        if t.powerup:
            self._apply_powerup(t, p, x, y)
            fx.explosion(x, y, t.color, 1.1)
            return
        playing = self.mode.phase == "play"
        if playing:
            self._credit_kill(p, t, x, y, h.perfect)
        if h.boss_down:
            fx.explosion(x, y, C.GOLD, 2.4, big=True)
            fx.add_shake(24)
            self.hitstop = 0.15
            if playing:
                self._boss_bonus(p, 1200 if t.parent.mini else 2500)
            a.audio.play("big_explode")
            return
        size = min(2.2, 0.5 + r / 55.0)
        fx.explosion(x, y, t.color, size, big=(t.kind in ("shielded", "node")))
        fx.add_shake(3 + 5 * size)
        self.hitstop = max(self.hitstop, 0.03)
        a.audio.play("explode", 0.55)

    def _apply_powerup(self, t, p: PlayerSlot, x, y):
        self.app.audio.play("powerup")
        if t.kind == "timewarp":
            self.warp = C.TIMEWARP_TIME
            self.fx.big_callout("TIME WARP", C.GOLD, 0.9)
        elif t.kind == "overcharge":
            p.overcharge = C.OVERCHARGE_TIME
            self.fx.big_callout(f"{p.label} OVERCHARGE", p.color, 0.9)
        else:                                                      # repair -> bonus points
            p.sc.add_points(500)
            self.team.add_points(500)
            self.fx.popup(x, y - 30, "+500", p.color, 30)

    # ---- events
    def _world_event(self, kind, t):
        mode = self.mode
        if kind == "pass" or mode.phase != "play":
            return
        sx, sy, _ = self.world.project(t)
        sx = min(max(sx, 80), C.W - 80)
        sy = min(max(sy, 100), C.H - 120)
        txt = mode.on_boss_leak(t) if kind == "boss_leak" else mode.on_leak(t)
        self.fx.ring(sx, sy, C.RED, 100, 0.5, 4)
        if self.coop:                                              # a miss costs the team
            self.fx.flash = 1.0
            self.fx.popup(sx, sy, txt, C.RED, 30, life=1.0)
            self.fx.add_shake(10)
            self.app.audio.play("leak")
        else:
            self.fx.popup(sx, sy, "MISSED", C.GREY, 22, life=0.8)

    def _mode_events(self):
        a, fx, mode = self.app, self.fx, self.mode
        for ev in mode.pop_events():
            name = ev[0]
            if name == "tick":
                a.audio.play("tick")
            elif name == "go":
                a.audio.play("go")
                fx.big_callout("GO!", C.GREEN, 0.9)
            elif name == "finished":
                for h in self.world.clear_all():
                    fx.explosion(h.sx, h.sy, h.target.color, 1.0, big=True)
                a.audio.play("level_clear")
                fx.big_callout("TIME!", C.GOLD, 1.8)
            elif name == "boss_warning":
                a.audio.play("alarm")
                fx.big_callout("WARNING", C.RED, 1.4)
            elif name == "emp_warn":
                a.audio.play("alarm")
                fx.big_callout("EMP INCOMING", C.RED, 1.0)
            elif name == "emp_on":
                a.audio.play("emp")
                fx.add_shake(8)

    def _finish(self):
        a = self.app
        S.record_play()
        p1, p2 = self.p
        pct = lambda p: f"{int(p.sc.accuracy * 100)}%"             # noqa: E731
        if self.coop:
            total = self.team.score
            rows = [("TEAM SCORE", f"{total:,}", C.GOLD),
                    ("KILLS  P1 / P2", f"{p1.sc.kills} / {p2.sc.kills}", C.CYAN),
                    ("ACCURACY  P1 / P2", f"{pct(p1)} / {pct(p2)}", C.GREEN),
                    ("BEST COMBO", f"{self.team.max_combo}", C.ORANGE),
                    ("MISSED", f"{self.team.leaks}", C.RED)]
            stats = dict(mode="coop", table="coop", easy=self.autofire, score=total,
                         accuracy=(p1.sc.accuracy + p2.sc.accuracy) / 2, max_combo=self.team.max_combo,
                         kills=self.team.kills, leaks=self.team.leaks, level=0, by_kind={}, perfects=0,
                         lives_end=None, rank=S.rank_for("timed", total, 0.7), celebrate=True,
                         title="TEAM RESULT", title_color=C.GOLD, rows=rows,
                         again=lambda: VersusScene(a, coop=True))
        else:
            s1, s2 = p1.sc.score, p2.sc.score
            win = 0 if s1 == s2 else (1 if s1 > s2 else 2)
            best = p1 if win != 2 else p2
            rows = [("P1 SCORE", f"{s1:,}", P_COLORS[0]), ("P2 SCORE", f"{s2:,}", P_COLORS[1]),
                    ("KILLS  P1 / P2", f"{p1.sc.kills} / {p2.sc.kills}", C.CYAN),
                    ("ACCURACY  P1 / P2", f"{pct(p1)} / {pct(p2)}", C.GREEN),
                    ("WINNER", "TIE" if win == 0 else f"PLAYER {win}", C.GOLD)]
            stats = dict(mode="versus", table="versus", easy=self.autofire, score=best.sc.score,
                         accuracy=best.sc.accuracy, max_combo=best.sc.max_combo, kills=best.sc.kills,
                         leaks=0, level=0, by_kind={}, perfects=0, lives_end=None,
                         rank=("=" if win == 0 else f"P{win}"),
                         rank_color=(C.GOLD if win == 0 else P_COLORS[win - 1]), celebrate=True,
                         title=("IT'S A TIE!" if win == 0 else f"PLAYER {win} WINS!"),
                         title_color=C.GOLD, rows=rows, again=lambda: VersusScene(a, coop=False))
        a.goto(ResultsScene(a, stats))

    # ---- draw
    def draw(self, dst):
        a, w, fx, mode = self.app, self.world, self.fx, self.mode
        now = a.clock_t
        speed = 0.5 + 0.9 * min(1.0, w.speed / 1.8)
        if a.cfg.gesture == "pinch":
            a.tunnel.set_theme(a.base_theme())
        else:
            a.tunnel.set_theme(int(self.age / 8.0) % 5)
        a.tunnel.draw(dst, w.scroll, speed, dt=0.0 if self.frozen else 1 / 60,
                      tint=None if w.time_scale > 0.9 else C.GOLD,
                      sway=((self.p[0].raw_x + self.p[1].raw_x) / 2 - C.CX) / (C.W / 2))
        locked_ids = {id(p.locked) for p in self.p if p.locked is not None}
        for t in sorted(w.targets, key=lambda t: -t.z):
            sx, sy, sr = w.project(t)
            if t.kind == "boss":
                draw_boss_tethers(dst, t, w, now)
            draw_target(dst, t, sx, sy, sr, now, locked=(id(t) in locked_ids))
        fx.draw(dst)
        self.offset = fx.shake_offset()
        for p in self.p:
            if not p.present and a.input.has_camera:
                continue
            charge_frac = min(1.0, p.held / C.CHARGE_HOLD) if p.sc.charge > 0 else 0.0
            ready = p.sc.charge > 0 and p.held >= C.CHARGE_HOLD
            draw_reticle(dst, p.rx, p.ry, now, p.fist, p.locked is not None, charge_frac, ready,
                         p.overcharge > 0, present=p.present, pulse=p.pulse, tint=p.color)
            draw_text(dst, p.label, 20, p.color, (p.rx + 40, p.ry - 40), glow=False)
        self._draw_hud(dst, now)
        if mode.emp > 0:
            for _ in range(14):
                y = self.jit.randrange(0, C.H)
                pygame.draw.line(dst, (80, 20, 30), (0, y), (C.W, y), self.jit.choice((1, 1, 2)))

    def _draw_hud(self, dst, now):
        mode = self.mode
        # player panels
        for p in self.p:
            x = 12 if p.idx == 0 else C.W - 12 - 300
            draw_hud_panel(dst, (x, 6, 300, 128), p.color)
            draw_text(dst, p.label if not self.coop else f"{p.label}  TEAM", 22, p.color, (x + 18, 18), "topleft",
                      glow=False)
            if not p.present and self.app.input.has_camera:
                draw_text(dst, "NO HAND", 22, C.ORANGE, (x + 282, 18), "topright", glow=False)
            draw_text(dst, f"{int(p.disp):,}", 46, C.WHITE, (x + 18, 40), "topleft")
            m = p.sc.multiplier
            draw_text(dst, f"x{m}", 34, mix(C.WHITE, p.color, 0.6), (x + 18, 92), "topleft")
            frac = 1.0 if m >= C.MAX_MULT else (p.sc.combo % C.COMBO_PER_STEP) / C.COMBO_PER_STEP
            draw_bar(dst, x + 84, 104, 120, 9, frac, p.color)
            for i in range(C.CHARGE_CELLS_MAX):
                cx = x + 232 + i * 22
                pygame.draw.circle(dst, C.ORANGE if i < p.sc.charge else (60, 50, 30), (cx, 108), 8,
                                   0 if i < p.sc.charge else 2)
        # centre: timer + intensity
        left = mode.time_left if mode.phase != "countdown" else mode.duration
        urgent = left <= 10 and mode.phase == "play"
        col = C.RED if urgent and int(now * 4) % 2 == 0 else (C.ORANGE if urgent else C.CYAN)
        pygame.draw.rect(dst, scale_color(col, 0.9), (0, 0, int(C.W * left / mode.duration), 6))
        draw_hud_panel(dst, (C.CX - 100, 6, 200, 98), C.CYAN)
        draw_text(dst, f"{int(math.ceil(left))}", 66, col, (C.CX, 52))
        draw_text(dst, "SECONDS", 18, C.GREY, (C.CX, 92), glow=False)
        if self.coop and mode.phase == "play":
            draw_text(dst, f"TEAM  {int(self.team.score):,}", 30, C.GOLD, (C.CX, 128), glow=False)
        elif mode.phase == "play":
            inten = mode.intensity
            draw_text(dst, f"INTENSITY  x{1.0 + 0.6 * inten:.1f}", 18, mix(C.CYAN, C.RED, inten), (C.CX, 124),
                      glow=False)
        self.menu_btn.draw(dst, now)
        self._draw_overlay(dst, now)

    def _draw_overlay(self, dst, now):
        mode, cx = self.mode, C.CX
        if self.frozen:
            p = 0.6 + 0.4 * math.sin(now * 6)
            draw_text(dst, "SHOW YOUR HANDS", 60, C.ORANGE, (cx, C.CY - 10), fade=p)
            return
        if mode.phase == "countdown":
            n = max(1, int(math.ceil(mode.cd)))
            title = "CO-OP" if self.coop else "VERSUS"
            draw_text(dst, title, 44, C.GOLD, (cx, C.CY - 150))
            draw_text(dst, str(n), 120, C.WHITE, (cx, C.CY - 40))
            for i, p in enumerate(self.p):
                ok = p.present or not self.app.input.has_camera
                label = f"{p.label}  READY" if ok else f"{p.label}  RAISE YOUR HAND"
                draw_text(dst, label, 28, p.color if ok else scale_color(C.ORANGE, 0.6 + 0.4 * math.sin(now * 6)),
                          (cx + (-250 if i == 0 else 250), C.CY + 60))
            how = ("Shoot targets together - misses cost the team" if self.coop
                   else "Highest score wins - shooting a mine costs YOU")
            draw_text(dst, how, 24, C.WHITE, (cx, C.CY + 120), glow=False)
            if self.autofire:
                draw_text(dst, "EASY: hold the reticle on a target and it fires", 22, C.GREY,
                          (cx, C.CY + 154), glow=False)
        if self.paused:
            veil = pygame.Surface((C.W, C.H), pygame.SRCALPHA)
            veil.fill((0, 0, 8, 190))
            dst.blit(veil, (0, 0))
            draw_text(dst, "PAUSED", 78, C.CYAN, (cx, 240))
            for b in self.pause_buttons:
                b.draw(dst, now)
            for p in self.p:                                 # pointers on top of the veil
                if p.present or not self.app.input.has_camera:
                    draw_reticle(dst, p.rx, p.ry, now, p.fist, False, 0, False, False, tint=p.color)

    def draw_overlay(self, dst):
        fx = self.fx
        if fx.flash > 0.02:
            v = self.app.tunnel.vign
            v.set_alpha(int(255 * min(1.0, fx.flash)))
            dst.blit(v, (0, 0))
