"""All screens: Calibrate, Menu (with live attract demo), Play, Results, High scores."""
from __future__ import annotations

import math
import random
import statistics

import pygame

from . import config as C
from . import scoring as S
from .fx import FX, add_glow, draw_text, mix, scale_color
from .fx import font as fx_font
from .inputs import BotInput, InputState
from .modes import EndlessMode, TimedMode
from .handdemo import draw_howto_panels
from .render import (draw_bar, draw_boss_tethers, draw_heart, draw_hud_panel,
                     draw_reticle, draw_target)
from .scoring import Scoring
from .ui import BigButton, Button, draw_panel, update_buttons
from .world import World

def gspec(app) -> dict:
    """Text/threshold spec of the currently selected fire gesture."""
    from .hand import GESTURES
    return GESTURES.get(getattr(app.cfg, "gesture", "fist"), GESTURES["fist"])


MULT_COLORS = [C.WHITE, C.CYAN, C.GREEN, C.GOLD, C.ORANGE, C.MAGENTA, C.PURPLE, C.RED]
RANK_COLORS = {"S": C.GOLD, "A": C.GREEN, "B": C.CYAN, "C": C.GREY}


class Scene:
    offset = (0, 0)

    def __init__(self, app):
        self.app = app
        self.age = 0.0

    def enter(self): ...
    def leave(self): ...
    def update(self, dt: float, inp: InputState): self.age += dt
    def draw(self, dst): ...
    def draw_overlay(self, dst): ...
    def on_key(self, key): ...


# =============================================================== PLAY
class PlayScene(Scene):
    def __init__(self, app, mode_key: str, demo: bool = False, seed=None, adaptive: bool = True,
                 on_finish=None, label: str | None = None):
        super().__init__(app)
        self.mode_key = mode_key
        self.demo = demo
        self.autofire = bool(app.cfg.autofire) and not demo
        self.table = mode_key + ("_easy" if self.autofire else "")   # high-score table key
        self.adaptive = adaptive
        self.seeded = seed is not None
        self.on_finish = on_finish       # Duel: receives the stats instead of showing the results screen
        self.label = label               # e.g. "PLAYER 1"
        self.rng = random.Random(seed)   # drives spawns only (so a seed gives identical targets) ...
        self.jit_rng = random.Random()   # ... effects / EMP jitter use their own generators
        self._new_run()
        # in-game MENU button: click-only (a long dwell is never reached), so aiming across it is safe
        self.menu_btn = Button((14, 158, 128, 42), "MENU", "menu", C.GREY, size=22, dwell=999.0)
        self.pause_buttons = [
            Button((C.CX - 190, 330, 380, 64), "RESUME", None, C.CYAN),
            Button((C.CX - 190, 410, 380, 64), "QUIT TO MENU", None, C.ORANGE),
        ]

    def _new_run(self):
        a = self.app
        self.world = World(self.rng)
        self.world.fair_spawns = self.seeded            # seeded runs (Duel) must not depend on the player
        self.scoring = Scoring()
        if self.mode_key == "timed":
            self.mode = TimedMode(self.world, self.scoring, self.rng,
                                  duration=a.cfg.duration, lives=a.cfg.timed_lives,
                                  adaptive=self.adaptive)
        else:
            self.mode = EndlessMode(self.world, self.scoring, self.rng)
        self.fx = FX(random.Random())
        self.bot = BotInput(self.world, skill=0.8, rng=random.Random(self.rng.random())) if self.demo else None
        if self.demo:
            self.mode.phase = "play"
        self.hitstop = 0.0
        self.warp = 0.0
        self.overcharge = 0.0
        self.held = 0.0
        self.charge_snd = False
        self.cool = 0.0
        self.lost = 0.0
        self.frozen = False
        self.paused = False
        self.end_timer = None
        self.disp_score = 0.0
        self.pulse = 0.0
        self.rx, self.ry = C.CX, C.CY
        self.fist_state = False
        self.locked = None
        self.jx = self.jy = 0.0
        self.emp_flicker = 0.0
        self.phase_banner = None
        self.lock_t = 0.0
        self.lock_target = None
        self.raw_x, self.raw_y = C.CX, C.CY
        self.best = (S.top_scores(self.table, 1) or [{}])[0]

    def enter(self):
        if not self.demo:
            self.app.audio.sfx_muted = False
            self.app.audio.music_start()

    # ---------------------------------------------------------------- update
    def update(self, dt, inp):
        self.age += dt
        a = self.app
        if self.demo:
            inp = self.bot.poll(dt)
        mode, world = self.mode, self.world

        if self.paused:
            self.rx, self.ry = inp.x, inp.y              # keep the pointer alive while paused
            self.fist_state = inp.fist and inp.present
            for i, b in enumerate(self.pause_buttons):
                if b.update(dt, inp, locked=self.age < 0.2):
                    a.audio.play("click")
                    if i == 0:
                        self.paused = False
                    else:
                        a.goto(MenuScene(a))
                        return
            return

        needs_hand = (not self.demo) and a.input.has_camera
        self.lost = 0.0 if inp.present else self.lost + dt
        self.frozen = needs_hand and self.lost > C.HAND_LOST_GRACE

        # reticle (with EMP interference)
        self.rx, self.ry = inp.x, inp.y
        fx_x, fx_y = inp.fire_x, inp.fire_y
        if mode.emp > 0:
            self.jx += (self.jit_rng.uniform(-45, 45) - self.jx) * min(1.0, dt * 18)
            self.jy += (self.jit_rng.uniform(-45, 45) - self.jy) * min(1.0, dt * 18)
            self.rx += self.jx
            self.ry += self.jy
            fx_x += self.jx
            fx_y += self.jy
            self.emp_flicker += dt
        else:
            self.jx = self.jy = 0.0
        self.rx = min(max(self.rx, 0), C.W)
        self.ry = min(max(self.ry, 0), C.H)
        self.raw_x, self.raw_y = self.rx, self.ry     # the hand itself (before any lock magnetism)

        self.pulse = max(0.0, self.pulse - dt * 5)
        if self.frozen:
            self.fx.update(dt * 0.3)
            return
        if self.hitstop > 0:
            self.hitstop -= dt
            self.fx.update(dt * 0.15)
            return
        self.fx.update(dt)
        if self.demo:                       # background attract run: no text clutter
            self.fx.texts.clear()
            self.fx.callout = None

        # power-ups
        if self.warp > 0:
            self.warp -= dt
        target_scale = C.TIMEWARP_SCALE if self.warp > 0 else 1.0
        world.time_scale += (target_scale - world.time_scale) * min(1.0, dt * 5)
        if self.overcharge > 0:
            self.overcharge -= dt

        # mode
        if self.end_timer is None:
            mode.update(dt, inp.present or not needs_hand)
            self._mode_events()
        elif not self.demo:
            self.end_timer -= dt
            if self.end_timer <= 0:
                self._finish()
                return
        else:
            self.end_timer -= dt
            if self.end_timer <= 0:
                self._new_run()
                return

        # in-game MENU button: opens the pause screen (Resume / Quit to menu)
        if not self.demo and self.end_timer is None and mode.phase != "countdown_hidden":
            if self.menu_btn.update(dt, inp, locked=self.age < 0.6):
                a.audio.play_ui("select")
                self.paused = True
                self.age = 0.0
                for b in self.pause_buttons:
                    b.progress = 0.0
                return
        over_menu = (not self.demo) and self.menu_btn.hovered(inp.fire_x, inp.fire_y)

        # shooting
        self.cool -= dt
        shooting_ok = self.end_timer is None
        self.fist_state = inp.fist and inp.present
        if shooting_ok:
            if self.autofire:
                self._auto_fire(dt)
            elif inp.fire and self.cool <= 0 and not over_menu:
                self.cool = C.SHOT_COOLDOWN
                self._shoot(fx_x, fx_y)
            self._charged_shot(dt, inp)

        # world
        for kind, t in world.update(dt):
            self._world_event(kind, t)

        self.locked = (self.lock_target if self.autofire
                       else world.target_at(self.rx, self.ry))
        self.disp_score += (self.scoring.score - self.disp_score) * min(1.0, dt * 9)

        # music intensity
        if not self.demo:
            if self.mode_key == "timed":
                inten = 0.1 + 0.9 * self.mode.intensity if mode.phase == "play" else 0.1
            else:
                inten = min(1.0, 0.15 + self.mode.level / 12.0) if mode.phase != "countdown" else 0.1
            a.audio.set_intensity(inten)

        if mode.over and self.end_timer is None:
            self.end_timer = 2.6

    # ---------------------------------------------------------------- shooting
    def _shoot(self, px, py, auto=False):
        a, fx, sc = self.app, self.fx, self.scoring
        spread = C.OVERCHARGE_SPREAD if self.overcharge > 0 else 0
        if auto and spread:
            # auto-fire must never shoot a mine: drop the side rays if either would catch one
            for ox in (-spread, spread):
                t = self.world.target_at(px + ox, py)
                if t is not None and t.hazard:
                    spread = 0
                    break
        hits = self.world.fire(px, py, spread)
        if self.mode.phase == "play":
            sc.shot(bool(hits))
        fx.tracer(C.CX, C.H + 10, px, py, C.PURPLE if spread else C.CYAN)
        if spread:
            for sgn in (-1, 1):
                fx.tracer(C.CX, C.H + 10, px + sgn * spread, py, C.PURPLE)
        a.audio.play("shoot", 0.7)
        self.pulse = 1.0
        if not hits:
            fx.burst(px, py, C.GREY, n=6, speed=(30, 140), life=(0.15, 0.3), size=(1, 2))
            return
        for h in hits:
            if auto:
                h.perfect = False          # auto-fire shoots dead centre: no "perfect" bonus
            self._on_hit(h)

    def _auto_fire(self, dt):
        """Easy mode.  Hands are not pixel-precise, so locking is forgiving:
          * a wide capture zone around the hand (bigger for big targets),
          * the lock is sticky - it survives the hand drifting well off the target,
          * the on-screen reticle is magnetically pulled onto the locked target,
          * the shot is fired at the target's centre, so a lock always hits.
        Mines are never locked."""
        w = self.world
        if self.mode.phase != "play":
            self.lock_t = 0.0
            self.lock_target = None
            return
        cand = w.lock_candidate(self.raw_x, self.raw_y, self.lock_target)
        if cand is not self.lock_target:
            self.lock_target = cand
            self.lock_t = 0.0
            if cand is not None:
                self.app.audio.play("tick", 0.3)         # lock-on blip
        if cand is None:
            return
        sx, sy, _sr = w.project(cand)
        self.lock_t += dt
        p = min(1.0, self.lock_t / C.AUTO_LOCK_TIME)
        pull = C.AUTO_MAGNET * (0.35 + 0.65 * p)
        self.rx += (sx - self.rx) * pull
        self.ry += (sy - self.ry) * pull
        if self.lock_t >= C.AUTO_LOCK_TIME and self.cool <= 0:
            self.cool = C.AUTO_FIRE_INTERVAL
            self._shoot(sx, sy, auto=True)

    def _on_hit(self, h):
        a, fx, sc, mode = self.app, self.fx, self.scoring, self.mode
        t = h.target
        x, y, r = h.sx, h.sy, max(10.0, h.sr)
        if t.hazard and h.killed and not h.blast:           # shot a mine
            txt = mode.on_mine(t)
            fx.explosion(x, y, C.RED, 1.4, big=True)
            fx.popup(x, y - 20, txt, C.RED, 34)
            fx.add_shake(20)
            fx.flash = 1.0
            a.audio.play("mine")
            self.hitstop = 0.08
            return
        if not h.killed:                                      # shielded: first hit
            fx.ring(x, y, C.WHITE, r * 1.6, 0.3, 3)
            fx.burst(x, y, C.WHITE, n=10, speed=(60, 240), life=(0.2, 0.4))
            a.audio.play("shield")
            return
        col = t.color
        if t.hazard:                                          # blasted a mine: safe
            fx.explosion(x, y, C.RED, 1.0)
            a.audio.play("pop")
            return
        if t.powerup:
            self._apply_powerup(t, x, y)
            fx.explosion(x, y, col, 1.1)
            return
        playing = mode.phase == "play"
        size = min(2.2, 0.5 + r / 55.0)
        if playing:
            pts, callout, cell = sc.kill(t.kind, t.pts, t.z, h.perfect)
            fx.popup(x, y - r - 8, f"+{pts:,}", C.GOLD if h.perfect else C.WHITE,
                     30 if h.perfect else 24)
            if h.perfect:
                fx.popup(x, y - r - 34, "PERFECT", C.GOLD, 18, life=0.7)
                fx.burst(x, y, C.GOLD, n=14, speed=(80, 360), life=(0.3, 0.6))
            if callout:
                fx.big_callout(callout, MULT_COLORS[min(7, sc.multiplier - 1)])
            if cell:
                fx.popup(C.CX, C.H - 130, "CHARGE CELL READY", C.ORANGE, 24, life=1.2, rise=30)
                a.audio.play("powerup", 0.6)
            a.audio.play_hit(min(9, sc.combo // 2))
        else:
            a.audio.play("explode", 0.6)
        if h.boss_down:
            fx.explosion(x, y, C.GOLD, 2.4, big=True)
            fx.add_shake(26)
            fx.flash_white = 1.0
            self.hitstop = 0.15
            if playing:
                bonus = 2500 if t.parent.mini is False else 1200
                sc.add_points(bonus)
                fx.big_callout("CORE DESTROYED", C.GOLD, 1.6)
                fx.popup(C.CX, C.CY, f"+{bonus:,}", C.GOLD, 48, life=1.4, rise=80)
            a.audio.play("big_explode")
            return
        fx.explosion(x, y, col, size, big=(t.kind in ("shielded", "node")))
        fx.add_shake(4 + 6 * size)
        self.hitstop = max(self.hitstop, 0.03)
        a.audio.play("explode", 0.55)

    def _apply_powerup(self, t, x, y):
        a, fx, mode = self.app, self.fx, self.mode
        a.audio.play("powerup")
        if t.kind == "timewarp":
            self.warp = C.TIMEWARP_TIME
            fx.big_callout("TIME WARP", C.GOLD, 0.9)
        elif t.kind == "overcharge":
            self.overcharge = C.OVERCHARGE_TIME
            fx.big_callout("OVERCHARGE", C.PURPLE, 0.9)
        elif t.kind == "repair":
            if mode.lives is not None and mode.lives < mode.max_lives:
                mode.lives += 1
                fx.big_callout("+1 LIFE", C.BLUE, 0.9)
            else:
                self.scoring.add_points(500)
                fx.popup(x, y - 30, "+500", C.BLUE, 30)
                fx.big_callout("REPAIR BONUS", C.BLUE, 0.9)

    def _charged_shot(self, dt, inp):
        sc, a = self.scoring, self.app
        if inp.fist and inp.present:
            self.held += dt
            if self.held > 0.06 and sc.charge > 0 and not self.charge_snd:
                self.charge_snd = True
                a.audio.play("charge", 0.6)
        else:
            if self.held >= C.CHARGE_HOLD and sc.charge > 0 and self.mode.phase == "play":
                self._release_blast()
            self.held = 0.0
            self.charge_snd = False

    def _release_blast(self):
        a, fx, sc = self.app, self.fx, self.scoring
        sc.charge -= 1
        hits = self.world.blast(self.rx, self.ry)
        fx.ring(self.rx, self.ry, C.ORANGE, C.BLAST_RADIUS, 0.55, 5)
        fx.ring(self.rx, self.ry, C.WHITE, C.BLAST_RADIUS * 0.7, 0.4, 2)
        fx.add_shake(18)
        fx.flash_white = 0.6
        a.audio.play("blast")
        self.hitstop = 0.06
        for h in hits:
            t = h.target
            if t.powerup:
                self._apply_powerup(t, h.sx, h.sy)
            elif not t.hazard:
                pts, _, _ = sc.kill(t.kind, t.pts, t.z, False)
                fx.popup(h.sx, h.sy - 20, f"+{pts:,}", C.ORANGE, 22)
            if h.boss_down:
                sc.add_points(1500)
                fx.big_callout("CORE DESTROYED", C.GOLD, 1.6)
            fx.explosion(h.sx, h.sy, t.color, 0.9)
        if hits:
            fx.big_callout("BLAST x%d" % len(hits), C.ORANGE, 0.9)

    # ---------------------------------------------------------------- events
    def _world_event(self, kind, t):
        a, fx, mode, sc = self.app, self.fx, self.mode, self.scoring
        if kind == "pass" or mode.phase != "play":
            return
        sx, sy, _ = self.world.project(t)
        sx = min(max(sx, 80), C.W - 80)
        sy = min(max(sy, 100), C.H - 120)
        had_combo = sc.combo
        if kind == "boss_leak":
            txt = mode.on_boss_leak(t)
            fx.add_shake(26)
            fx.big_callout("CORE BREACH", C.RED, 1.4)
        else:
            txt = mode.on_leak(t)
            fx.add_shake(14)
        fx.flash = 1.0
        fx.popup(sx, sy, txt, C.RED, 34, life=1.1)
        if had_combo >= 5:
            fx.popup(sx, sy + 34, "COMBO LOST", C.ORANGE, 20, life=1.0)
        fx.ring(sx, sy, C.RED, 120, 0.5, 4)
        a.audio.play("leak")

    def _mode_events(self):
        a, fx, mode = self.app, self.fx, self.mode
        for ev in mode.pop_events():
            name = ev[0]
            if name == "tick":
                a.audio.play("tick")
            elif name == "go":
                a.audio.play("go")
                if mode.key == "timed":
                    fx.big_callout("GO!", C.GREEN, 0.9)
            elif name == "level_intro":
                a.audio.play("tick", 0.7)
            elif name == "level_clear":
                self._pop_all()
                a.audio.play("level_clear")
            elif name == "game_over":
                self._pop_all(big=True)
                a.audio.play("game_over")
                fx.add_shake(24)
                fx.flash = 1.0
            elif name == "finished":
                self._pop_all(big=True)
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

    def _pop_all(self, big=False):
        for h in self.world.clear_all():
            self.fx.explosion(h.sx, h.sy, h.target.color, 1.0, big=big)

    def _finish(self):
        a, sc, mode = self.app, self.scoring, self.mode
        if self.demo:
            return
        level = mode.level if self.mode_key == "endless" else 0
        S.record_play()
        stats = dict(mode=self.mode_key, table=self.table, easy=self.autofire,
                     score=sc.score, accuracy=sc.accuracy,
                     max_combo=sc.max_combo, kills=sc.kills, leaks=sc.leaks,
                     level=level, by_kind=dict(sc.by_kind), perfects=sc.perfects,
                     lives_end=mode.lives, rank=S.rank_for(self.mode_key, sc.score,
                                                           sc.accuracy, level))
        if self.on_finish is not None:
            self.on_finish(stats)
            return
        a.goto(ResultsScene(a, stats))

    def on_key(self, key):
        if self.demo:
            return
        if key in (pygame.K_ESCAPE, pygame.K_p) and self.end_timer is None:
            self.paused = not self.paused
            self.age = 0.0
            for b in self.pause_buttons:
                b.progress = 0.0

    # ---------------------------------------------------------------- draw
    def draw(self, dst):
        a, w, fx, mode = self.app, self.world, self.fx, self.mode
        now = a.clock_t
        speed = 0.5 + 0.9 * min(1.0, w.speed / 1.8)
        a.tunnel.set_theme(self._theme_index())
        a.tunnel.draw(dst, w.scroll, speed, dt=1 / 60 if not self.frozen else 0.0,
                      tint=None if w.time_scale > 0.9 else C.GOLD,
                      sway=(self.raw_x - C.CX) / (C.W / 2))
        # targets far -> near
        for t in sorted(w.targets, key=lambda t: -t.z):
            sx, sy, sr = w.project(t)
            if t.kind == "boss":
                draw_boss_tethers(dst, t, w, now)
            draw_target(dst, t, sx, sy, sr, now, locked=(t is self.locked))
        if self.autofire and not self.demo:
            self._draw_autolock(dst, now)
        fx.draw(dst)
        self.offset = fx.shake_offset()
        if not self.demo:
            charge_frac = min(1.0, self.held / C.CHARGE_HOLD) if self.scoring.charge > 0 else 0.0
            ready = self.scoring.charge > 0 and self.held >= C.CHARGE_HOLD
            draw_reticle(dst, self.rx, self.ry, now, self.fist_state, self.locked is not None,
                         charge_frac, ready, self.overcharge > 0,
                         present=not self.frozen, pulse=self.pulse)
            self._draw_hud(dst, now)
        if mode.emp > 0:
            rng = self.jit_rng
            for _ in range(14):
                y = rng.randrange(0, C.H)
                pygame.draw.line(dst, (80, 20, 30), (0, y), (C.W, y), rng.choice((1, 1, 2)))
        if self.paused and not self.demo:                 # the pause veil must not hide the pointer
            draw_reticle(dst, self.rx, self.ry, now, self.fist_state, False, 0, False, False)

    def _draw_autolock(self, dst, now):
        """Easy mode feedback: a dotted capture zone around your hand, and when locked a
        beam + progress ring on the target."""
        tgt = self.lock_target
        if tgt is None or not tgt.alive:
            # faint dotted capture zone so players see how forgiving the lock is
            r = C.AUTO_CAPTURE_PX
            for i in range(20):
                a = now * 0.6 + i * math.tau / 20
                pygame.draw.circle(dst, (22, 70, 48),
                                   (int(self.raw_x + math.cos(a) * r), int(self.raw_y + math.sin(a) * r)), 2)
            return
        sx, sy, sr = self.world.project(tgt)
        p = min(1.0, self.lock_t / C.AUTO_LOCK_TIME)
        pygame.draw.line(dst, (30, 160, 90), (self.raw_x, self.raw_y), (sx, sy), 1)
        rr = int(sr * 1.55 + 14)
        rect = pygame.Rect(0, 0, rr * 2, rr * 2)
        rect.center = (int(sx), int(sy))
        pygame.draw.circle(dst, (20, 80, 50), rect.center, rr, 1)
        if p > 0.02:
            pygame.draw.arc(dst, C.GREEN, rect, math.pi / 2, math.pi / 2 + math.tau * p, 4)

    def _draw_hud(self, dst, now):
        a, sc, mode = self.app, self.scoring, self.mode
        self.menu_btn.draw(dst, now)
        if self.label:
            draw_text(dst, self.label, 26, C.GOLD, (C.CX, 168), glow=False)
        # ---- score block
        draw_hud_panel(dst, (12, 6, 312, 138), C.CYAN)
        draw_text(dst, "SCORE", 18, C.GREY, (28, 14), "topleft", glow=False)
        draw_text(dst, f"{int(self.disp_score):,}", 54, C.WHITE, (28, 30), "topleft")
        m = sc.multiplier
        mc = MULT_COLORS[min(7, m - 1)]
        draw_text(dst, f"x{m}", 44, mc, (28, 90), "topleft")
        step = C.COMBO_PER_STEP
        frac = 1.0 if m >= C.MAX_MULT else (sc.combo % step) / step
        draw_bar(dst, 100, 104, 150, 10, frac, mc)
        if sc.combo > 1:
            draw_text(dst, f"COMBO {sc.combo}", 18, scale_color(mc, 0.9), (100, 120), "topleft", glow=False)

        # ---- centre
        if self.mode_key == "timed":
            draw_hud_panel(dst, (C.CX - 100, 6, 200, 98), C.CYAN)
            left = mode.time_left if mode.phase != "countdown" else mode.duration
            frac = left / mode.duration
            urgent = left <= 10 and mode.phase == "play"
            col = C.RED if urgent and int(now * 4) % 2 == 0 else (C.ORANGE if urgent else C.CYAN)
            pygame.draw.rect(dst, scale_color(col, 0.9), (0, 0, int(C.W * frac), 6))
            draw_text(dst, f"{int(math.ceil(left))}", 66, col, (C.CX, 52))
            draw_text(dst, "SECONDS", 16, C.GREY, (C.CX, 92), glow=False)
            if mode.phase == "play":                      # adaptive difficulty meter
                inten = mode.intensity
                icol = mix(C.CYAN, C.RED, inten)
                draw_text(dst, f"INTENSITY   x{1.0 + 0.6 * inten:.1f} POINTS", 18, icol, (C.CX, 124),
                          glow=False)
                draw_bar(dst, C.CX - 110, 142, 220, 8, inten, icol)
        else:
            draw_hud_panel(dst, (C.CX - 140, 6, 280, 96), C.CYAN)
            draw_text(dst, f"LEVEL {mode.level}", 46, C.CYAN, (C.CX, 44))
            left = len(mode.queue) + self.world.active_count() + \
                (1 if (mode.spec.get("boss") and not mode.boss_done) else 0)
            if mode.phase == "play":
                draw_text(dst, f"TARGETS LEFT  {left}", 18, C.GREY, (C.CX, 82), glow=False)

        # ---- right: lives / best
        if mode.lives is not None:
            n = max(mode.max_lives if self.mode_key == "timed" else 3, mode.lives)
            pw = n * 46 + 22
            draw_hud_panel(dst, (C.W - 12 - pw, 6, pw, 70), C.RED)
            for i in range(n):
                draw_heart(dst, C.W - 40 - (n - 1 - i) * 46, 42, 36,
                           C.RED if i < mode.lives else C.GREY, filled=i < mode.lives)
        best = self.best.get("score") if self.best else None
        if best:
            draw_text(dst, f"BEST {best:,}", 18, C.GREY, (C.W - 28, 78), "topright", glow=False)
        if self.autofire:
            draw_text(dst, "EASY - AUTO FIRE", 16, C.GREEN, (C.W - 28, 100), "topright", glow=False)

        # ---- bottom-left: charge cells
        draw_hud_panel(dst, (12, C.H - 84, 292, 72), C.ORANGE)
        for i in range(C.CHARGE_CELLS_MAX):
            cx, cy = 44 + i * 40, C.H - 56
            filled = i < sc.charge
            pts = [(cx + 15 * math.cos(math.radians(60 * k + 30)),
                    cy + 15 * math.sin(math.radians(60 * k + 30))) for k in range(6)]
            if filled:
                add_glow(dst, cx, cy, C.ORANGE, 30, 0.5)
                pygame.draw.polygon(dst, C.ORANGE, pts)
            else:
                pygame.draw.polygon(dst, scale_color(C.ORANGE, 0.3), pts, 2)
        draw_text(dst, f"HOLD {gspec(self.app)['label']} + OPEN = BLAST", 18, C.ORANGE,
                  (28, C.H - 36), "topleft", glow=False)

        # ---- power-up timers
        bars = []
        if self.warp > 0:
            bars.append(("TIME WARP", self.warp / C.TIMEWARP_TIME, C.GOLD))
        if self.overcharge > 0:
            bars.append(("OVERCHARGE", self.overcharge / C.OVERCHARGE_TIME, C.PURPLE))
        for i, (lbl, f, col) in enumerate(bars):
            y = C.H - 62 - i * 36
            draw_text(dst, lbl, 16, col, (C.CX, y - 14), glow=False)
            draw_bar(dst, C.CX - 90, y, 180, 10, f, col)

        self._draw_phase_overlay(dst, now)

    def _draw_phase_overlay(self, dst, now):
        mode, a = self.mode, self.app
        cx = C.CX
        if self.frozen:
            p = 0.6 + 0.4 * math.sin(now * 6)
            draw_text(dst, "SHOW YOUR HAND", 60, C.ORANGE, (cx, C.CY - 10), fade=p)
            draw_text(dst, "tracking lost - game paused", 22, C.GREY, (cx, C.CY + 44), glow=False)
            return
        if mode.phase == "countdown":
            n = max(1, int(math.ceil(mode.cd)))
            frac = mode.cd - math.floor(mode.cd)
            size = int(120 + 50 * frac)
            if self.mode_key == "timed":
                draw_text(dst, "GET READY", 34, C.CYAN, (cx, C.CY - 120))
                draw_text(dst, str(n), size, C.WHITE, (cx, C.CY - 20))
                draw_text(dst, self._controls_hint(), 26, C.WHITE,
                          (cx, C.CY + 90), glow=False)
                draw_text(dst, "Try a shot now - the run starts slowly and ramps up", 20, C.GREY,
                          (cx, C.CY + 128), glow=False)
            else:
                draw_text(dst, "SURVIVAL", 44, C.CYAN, (cx, C.CY - 110))
                draw_text(dst, "3 LIVES. A TARGET GETS PAST = LOSE A LIFE.", 22, C.WHITE,
                          (cx, C.CY - 60), glow=False)
                draw_text(dst, str(n), size, C.WHITE, (cx, C.CY + 20))
                draw_text(dst, self._controls_hint(), 24, C.GREY,
                          (cx, C.CY + 120), glow=False)
        elif mode.phase == "intro":
            f = 1.0 - mode.timer / C.LEVEL_INTRO_TIME
            draw_text(dst, f"LEVEL {mode.level}", int(88 + 20 * (1 - f)), C.CYAN,
                      (cx, C.CY - 70), fade=min(1.0, 3 * (1 - abs(f * 2 - 1) + 0.2)))
            draw_text(dst, mode.spec["note"], 30, C.WHITE, (cx, C.CY - 6), glow=False)
        elif mode.phase == "clear":
            info = mode.cleared_info
            draw_text(dst, f"LEVEL {info['level']} CLEAR", 66, C.GREEN, (cx, C.CY - 90))
            draw_text(dst, f"BONUS  +{info['bonus']:,}", 34, C.GOLD, (cx, C.CY - 20))
            draw_text(dst, f"ACCURACY {int(info['accuracy'] * 100)}%"
                           + ("   FLAWLESS" if info["flawless"] else ""),
                      24, C.WHITE, (cx, C.CY + 26), glow=False)
            if info["life"]:
                draw_text(dst, "+1 LIFE", 30, C.RED, (cx, C.CY + 68))
            nxt = level_note(info["level"] + 1)
            draw_text(dst, f"NEXT: {nxt}", 22, C.GREY, (cx, C.CY + 112), glow=False)
        if self.paused:
            veil = pygame.Surface((C.W, C.H), pygame.SRCALPHA)
            veil.fill((0, 0, 8, 190))
            dst.blit(veil, (0, 0))
            draw_text(dst, "PAUSED", 78, C.CYAN, (cx, 240))
            for b in self.pause_buttons:
                b.draw(dst, now)

    def _theme_index(self) -> int:
        """Normal modes keep cycling through the colour worlds (neon, toxic, inferno, void, ice)
        with smooth fades; Survival also steps to the next world at every level. Clock It mode
        stays in its pink-purple world."""
        if self.app.cfg.gesture == "pinch":
            return self.app.base_theme()
        t = self.age
        if self.mode_key == "endless":
            return (max(0, self.mode.level - 1) + int(t / 10.0)) % 5
        return int(t / 8.0) % 5

    def _controls_hint(self):
        if self.autofire:
            return "MOVE YOUR HAND TO AIM  -  SHOTS FIRE AUTOMATICALLY"
        return f"OPEN HAND = AIM      {gspec(self.app)['verb']} = SHOOT"

    def draw_overlay(self, dst):
        fx = self.fx
        if fx.flash > 0.02:
            v = self.app.tunnel.vign
            v.set_alpha(int(255 * min(1.0, fx.flash)))
            dst.blit(v, (0, 0))
        if fx.flash_white > 0.02:
            s = pygame.Surface((C.W, C.H))
            s.fill((255, 255, 255))
            s.set_alpha(int(110 * fx.flash_white))
            dst.blit(s, (0, 0))


def level_note(n: int) -> str:
    from .modes import level_spec
    return level_spec(n)["note"]


# =============================================================== HOME (attract screen)
def _who(entry) -> str:
    n = (entry or {}).get("name", "")
    return "ANONYMOUS" if n in ("", "---") else n


class MenuScene(Scene):
    """The attract / home screen. One big PLAY circle: hold your hand in it to start."""

    show_guide = True
    pip_scale = 1.0

    def __init__(self, app):
        super().__init__(app)
        self.demo = PlayScene(app, "timed", demo=True)
        self.play_btn = BigButton((C.CX, 452), 118, "PLAY", "play")
        y = C.H - 70
        self.buttons = [
            Button((C.CX - 250, y, 150, 46), "SCORES", "scores", C.GOLD, size=20),
            Button((C.CX - 75, y, 150, 46), "SETTINGS", "settings", C.CYAN, size=20),
            Button((C.CX + 100, y, 150, 46), "QUIT", "quit", C.GREY, size=20),
        ]
        keys = ("timed", "timed_easy")
        self.today = S.best_entry(keys, S.start_of_today())
        self.ever = S.best_entry(keys)
        self._inp = None

    def enter(self):
        a = self.app
        a.audio.sfx_muted = True          # the demo behind the menu stays silent ...
        a.audio.music_start()             # ... but menu sounds use play_ui and still play
        a.audio.set_intensity(0.35)

    def leave(self):
        self.app.audio.sfx_muted = False

    def on_key(self, key):
        if key in (pygame.K_RETURN, pygame.K_SPACE):
            self.app.goto(PickScene(self.app))

    def update(self, dt, inp):
        super().update(dt, inp)
        self.demo.update(dt, inp)
        a = self.app
        self._inp = inp
        locked = self.age < C.UI_LOCK_TIME
        if self.play_btn.update(dt, inp, locked):
            a.audio.play_ui("select")
            a.goto(PickScene(a))
            return
        b = update_buttons(self.buttons, dt, inp, locked, a.audio)
        if b is not None:
            if b.action == "scores":
                a.goto(ScoresScene(a))
            elif b.action == "settings":
                a.goto(SettingsScene(a))
            elif b.action == "quit":
                a.running = False

    def draw(self, dst):
        a = self.app
        now = a.clock_t
        self.demo.draw(dst)
        veil = pygame.Surface((C.W, C.H), pygame.SRCALPHA)
        veil.fill((2, 3, 14, 150))
        dst.blit(veil, (0, 0))
        f88 = fx_font(88)
        w1, w2 = f88.size("NEURAL ")[0], f88.size("TARGETING")[0]
        x0 = C.CX - (w1 + w2) // 2
        draw_text(dst, "NEURAL ", 88, C.CYAN, (x0, 92), "midleft")
        draw_text(dst, "TARGETING", 88, C.MAGENTA, (x0 + w1, 92), "midleft")
        draw_text(dst, "PLAY WITH YOUR HANDS  -  NO CONTROLLER NEEDED", 24, C.GOLD, (C.CX, 152), glow=False)
        n_today = S.plays_today()
        if n_today:
            draw_text(dst, f"{n_today} GAMES PLAYED TODAY", 20, C.GREEN, (C.W - 24, 24), "topright", glow=False)
        draw_howto_panels(dst, now, a.cfg.autofire, a.cfg.gesture)
        # the event scoreboard teaser
        if self.today:
            draw_text(dst, f"TODAY'S TOP PLAYER   {_who(self.today)}   {self.today['score']:,}", 28, C.GOLD,
                      (C.CX, 208))
        else:
            draw_text(dst, "NO SCORE YET TODAY  -  BE THE FIRST!", 26, C.GOLD, (C.CX, 208), glow=False)
        if self.ever and self.ever is not self.today and (not self.today or self.ever["score"] > self.today["score"]):
            draw_text(dst, f"BEST EVER   {_who(self.ever)}   {self.ever['score']:,}", 20, C.GREY,
                      (C.CX, 244), glow=False)
        # the big PLAY circle
        has_cam = a.input.has_camera
        inp = self._inp
        present = inp.present if inp is not None else True
        if has_cam and not present:
            p = 0.55 + 0.45 * math.sin(now * 6)
            self.play_btn.sub = "RAISE YOUR HAND"
            self.play_btn.sub_color = scale_color(C.ORANGE, p)
            draw_text(dst, "STEP UP  -  RAISE YOUR HAND", 34, C.ORANGE, (C.CX, 290), fade=0.5 + 0.5 * p)
        else:
            self.play_btn.sub = "HOLD HAND HERE"
            self.play_btn.sub_color = None
            draw_text(dst, "HOLD YOUR HAND IN THE CIRCLE TO PLAY", 30, C.GREEN, (C.CX, 290))
        self.play_btn.draw(dst, now)
        how = "pinch" if a.cfg.gesture == "pinch" else "close your fist"
        draw_text(dst, f"or {how} while you are on the circle", 18, C.GREY, (C.CX, 604), glow=False)
        for b in self.buttons:
            b.draw(dst, now)
        if inp is not None:
            draw_reticle(dst, inp.x, inp.y, now, inp.fist, False, 0, False, False, present=inp.present)

    def draw_overlay(self, dst):
        self.demo.draw_overlay(dst)


# =============================================================== PICK (mode / difficulty / fire gesture)
class PickScene(Scene):
    """Game cards, plus simple either/or choices for players, difficulty and fire gesture."""

    show_guide = True
    pip_scale = 1.0

    CARDS_1P = (("timed", "SCORE ATTACK", C.CYAN, "60 seconds. Hit as many", "targets as you can."),
                ("endless", "SURVIVAL", C.MAGENTA, "3 lives. Clear level after", "level. How far can you get?"))
    CARDS_2P = (("versus", "VERSUS", C.CYAN, "Both at once.", "Highest score wins."),
                ("coop", "CO-OP", C.GREEN, "Both at once. One", "shared team score."),
                ("duel", "DUEL", C.GOLD, "Take turns on the", "SAME targets."))

    def __init__(self, app):
        super().__init__(app)
        cx = C.CX
        self.players = [
            Button((cx - 232, 80, 222, 48), "1 PLAYER", "p1", C.CYAN, size=26),
            Button((cx + 10, 80, 222, 48), "2 PLAYERS", "p2", C.MAGENTA, size=26),
        ]
        self.modes: list[Button] = []
        self.diff = [
            Button((cx - 262, 420, 252, 72), "EASY", "easy", C.GREEN, size=30, sub="you aim - it fires for you"),
            Button((cx + 10, 420, 252, 72), "NORMAL", "normal", C.CYAN, size=30, sub="you aim and shoot"),
        ]
        self.fire = [
            Button((cx - 262, 546, 252, 72), "FIST", "fist", C.ORANGE, size=30, sub="close your whole hand"),
            Button((cx + 10, 546, 252, 72), "CLOCK IT", "pinch", C.ORANGE, size=30, sub="pinch thumb + index"),
        ]
        self.back = Button((24, C.H - 84, 190, 58), "BACK", "back", C.GREY, size=24)
        self._sync()

    def _cards(self):
        return self.CARDS_2P if self.app.want_players == 2 else self.CARDS_1P

    def _build_cards(self):
        cards = self._cards()
        n = len(cards)
        w = 430 if n == 2 else 300
        gap = 20
        x0 = C.CX - (n * w + (n - 1) * gap) // 2
        self.modes = [Button((x0 + i * (w + gap), 142, w, 196), "", c[0], c[2]) for i, c in enumerate(cards)]

    def _sync(self):
        a = self.app
        two = a.want_players == 2
        self.players[0].selected = not two
        self.players[1].selected = two
        self.diff[0].selected = bool(a.cfg.autofire)
        self.diff[1].selected = not a.cfg.autofire
        self.fire[0].selected = a.cfg.gesture == "fist"
        self.fire[1].selected = a.cfg.gesture == "pinch"
        for b in self.fire:
            b.enabled = a.input.has_camera
        self._build_cards()

    def enter(self):
        self.app.audio.sfx_muted = False
        self.app.audio.music_start()

    def on_key(self, key):
        if key == pygame.K_ESCAPE:
            self.app.goto(MenuScene(self.app))

    def _start(self, mode: str):
        a = self.app
        if mode in ("versus", "coop", "duel"):
            from .versus import DuelFlow, DuelInterlude, VersusScene
            if mode == "duel":
                flow = DuelFlow(a)
                make = lambda: DuelInterlude(a, flow, 1)            # noqa: E731
            else:
                make = lambda: VersusScene(a, coop=(mode == "coop"))   # noqa: E731
        else:
            make = lambda: PlayScene(a, mode)                       # noqa: E731
        if a.input.has_camera and not a.session_trial_done:
            a.goto(TrialScene(a, make))            # a quick 10-second "try it" for new players
        else:
            a.goto(make())

    def update(self, dt, inp):
        super().update(dt, inp)
        a = self.app
        self._inp = inp
        locked = self.age < C.UI_LOCK_TIME
        row = (self.players if C.TWO_PLAYER else []) + self.modes + self.diff + self.fire + [self.back]
        b = update_buttons(row, dt, inp, locked, a.audio)
        if b is None:
            return
        act = b.action
        if act in ("p1", "p2"):
            a.want_players = 2 if act == "p2" else 1
            self._sync()
        elif act in ("timed", "endless", "versus", "coop", "duel"):
            self._start(act)
        elif act == "easy" or act == "normal":
            a.cfg.autofire = act == "easy"
            a.cfg.save()
            self._sync()
        elif act in ("fist", "pinch"):
            a.set_gesture(act)
            self._sync()
        elif act == "back":
            a.goto(MenuScene(a))

    def _best(self, mode):
        if mode in ("timed", "endless"):
            table = mode + ("_easy" if self.app.cfg.autofire else "")
        else:
            table = mode
        e = (S.top_scores(table, 1) or [None])[0]
        if not e:
            return "NO SCORE YET"
        return f"BEST  LEVEL {e.get('level', 0)}" if mode == "endless" else f"BEST  {e['score']:,}"

    def draw(self, dst):
        a = self.app
        now = a.clock_t
        a.tunnel.set_theme(a.base_theme())
        a.tunnel.draw(dst, now * 5, 0.35, dt=1 / 60)
        veil = pygame.Surface((C.W, C.H), pygame.SRCALPHA)
        veil.fill((2, 3, 14, 110))
        dst.blit(veil, (0, 0))
        draw_text(dst, "CHOOSE YOUR GAME", 46 if C.TWO_PLAYER else 50, C.CYAN, (C.CX, 44 if C.TWO_PLAYER else 64))
        if C.TWO_PLAYER:
            for b in self.players:
                b.draw(dst, now)
        for btn, card in zip(self.modes, self._cards()):
            mode, title, _col, l1, l2 = card
            btn.draw(dst, now)
            r = btn.rect
            draw_text(dst, title, 42 if len(self.modes) == 2 else 38, btn.color, (r.centerx, r.top + 54))
            draw_text(dst, l1, 21, C.WHITE, (r.centerx, r.top + 108), glow=False)
            draw_text(dst, l2, 21, C.WHITE, (r.centerx, r.top + 138), glow=False)
            draw_text(dst, self._best(mode), 22, C.GOLD, (r.centerx, r.bottom - 34), glow=False)
        draw_text(dst, "DIFFICULTY", 20, C.GREY, (C.CX, 398), glow=False)
        for b in self.diff:
            b.draw(dst, now)
        cam = a.input.has_camera
        note = ("FIRE WITH" if not a.cfg.autofire else "FIRE WITH   (in EASY only used for the charged blast)")
        if not cam:
            note = "FIRE WITH   (needs the camera)"
        draw_text(dst, note, 20, C.GREY, (C.CX, 524), glow=False)
        for b in self.fire:
            b.draw(dst, now)
        if a.want_players == 2:
            msg = "stand side by side: P1 on the LEFT of the camera view, P2 on the RIGHT"
            if not cam:
                msg = "no camera: P1 = mouse, P2 = arrow keys + Enter"
            draw_text(dst, msg, 20, C.MAGENTA, (C.CX, 640), glow=False)
        self.back.draw(dst, now)
        inp = getattr(self, "_inp", None)
        if inp is not None:
            draw_reticle(dst, inp.x, inp.y, now, inp.fist, False, 0, False, False, present=inp.present)


# =============================================================== SETTINGS
class SettingsScene(Scene):
    show_guide = False
    pip_scale = 0.75

    def __init__(self, app):
        super().__init__(app)
        cx = C.CX
        acts = (("fullscreen", C.CYAN), ("preview", C.CYAN), ("sound", C.CYAN),
                ("calibrate", C.GREEN), ("tutorial", C.CYAN))
        self.buttons = [Button((cx - 270, 140 + i * 78, 540, 64), "", act, col, size=26)
                        for i, (act, col) in enumerate(acts)]
        self.back = Button((cx - 130, 140 + 5 * 78 + 6, 260, 58), "BACK", "back", C.GREY, size=26)
        self._sync()

    def _sync(self):
        a = self.app
        lab = {
            "fullscreen": f"FULLSCREEN:  {'ON' if a.cfg.fullscreen else 'OFF'}",
            "preview": f"CAMERA VIEW:  {'SHOWN' if a.show_pip else 'HIDDEN'}",
            "sound": f"SOUND:  {'OFF' if a.audio.muted else 'ON'}",
            "calibrate": "CALIBRATE YOUR HAND  (optional)",
            "tutorial": "HOW TO PLAY  (full tutorial)",
        }
        for b in self.buttons:
            b.label = lab[b.action]
        self.buttons[3].enabled = a.input.has_camera
        self.buttons[1].enabled = a.input.has_camera

    def on_key(self, key):
        if key == pygame.K_ESCAPE:
            self.app.goto(MenuScene(self.app))

    def update(self, dt, inp):
        super().update(dt, inp)
        a = self.app
        self._inp = inp
        b = update_buttons(self.buttons + [self.back], dt, inp, self.age < C.UI_LOCK_TIME, a.audio)
        if b is None:
            return
        act = b.action
        if act == "fullscreen":
            a.toggle_fullscreen()
        elif act == "preview":
            a.set_preview(not a.show_pip)
        elif act == "sound":
            a.audio.toggle_mute()
        elif act == "calibrate":
            a.goto(CalibrateScene(a, then_menu=True))
            return
        elif act == "tutorial":
            a.goto(TutorialScene(a))
            return
        elif act == "back":
            a.goto(MenuScene(a))
            return
        self._sync()

    def draw(self, dst):
        a = self.app
        now = a.clock_t
        a.tunnel.set_theme(a.base_theme())
        a.tunnel.draw(dst, now * 5, 0.3, dt=1 / 60)
        draw_text(dst, "SETTINGS", 60, C.CYAN, (C.CX, 76))
        for b in self.buttons + [self.back]:
            b.draw(dst, now)
        inp = getattr(self, "_inp", None)
        if inp is not None:
            draw_reticle(dst, inp.x, inp.y, now, inp.fist, False, 0, False, False, present=inp.present)


# =============================================================== TRIAL (10-second "try it")
class TrialScene(Scene):
    """A very short warm-up for new players: aim at one cube, shoot it, go. Never longer than
    TRIAL_TIMEOUT seconds, and skippable."""

    show_guide = False
    pip_scale = 0.75

    def __init__(self, app, make_next):
        super().__init__(app)
        self.make_next = make_next
        self.rng = random.Random()
        self.world = World(self.rng)
        self.world.base_speed = 0.26
        self.fx = FX(self.rng)
        self.phase = "aim"               # aim -> shoot -> nice
        self.t = 0.0
        self.nice_t = 0.0
        self.cool = 0.0
        self.lock_t = 0.0
        self.rx, self.ry = C.CX, C.CY
        self.cube = None
        self.skip_btn = Button((24, C.H - 84, 190, 58), "SKIP", "skip", C.GREY, size=24)
        self._spawn()

    def enter(self):
        self.app.audio.sfx_muted = False
        self.app.audio.music_start()
        self.app.audio.set_intensity(0.2)

    def _spawn(self):
        x = self.rng.choice((-1, 1)) * self.rng.uniform(0.5, 1.1)
        self.cube = self.world.spawn("standard", x, self.rng.uniform(-0.4, 0.4), 18.0)
        self.cube.r = 0.62                                 # big and easy to see for a first target

    def _finish(self):
        a = self.app
        a.session_trial_done = True
        a.goto(self.make_next())

    def on_key(self, key):
        if key == pygame.K_ESCAPE:
            self._finish()

    def update(self, dt, inp):
        super().update(dt, inp)
        a, w, fx = self.app, self.world, self.fx
        self.t += dt
        self.rx, self.ry = inp.x, inp.y
        self.cool -= dt
        fx.update(dt)
        for kind, _t in w.update(dt):
            if kind == "leak" and self.phase != "nice":
                self._spawn()                              # it slipped past: bring another
        if self.skip_btn.update(dt, inp, locked=self.age < 0.8):
            a.audio.play_ui("select")
            self._finish()
            return
        if self.phase == "nice":
            self.nice_t -= dt
            if self.nice_t <= 0:
                self._finish()
            return
        if self.t > C.TRIAL_TIMEOUT:
            self._finish()
            return
        cube = self.cube
        if cube is not None and cube.alive and inp.present:
            sx, sy, sr = w.project(cube)
            near = math.hypot(self.rx - sx, self.ry - sy) < max(sr * 2.4, 70)
            if near and self.phase == "aim":
                self.phase = "shoot"
                a.audio.play_ui("hover", 0.8)
            elif not near and self.phase == "shoot":
                self.phase = "aim"
        shot = None
        if a.cfg.autofire:
            cand = w.lock_candidate(self.rx, self.ry)
            if cand is None:
                self.lock_t = 0.0
            else:
                self.lock_t += dt
                if self.lock_t >= C.AUTO_LOCK_TIME and self.cool <= 0:
                    self.cool = C.AUTO_FIRE_INTERVAL
                    sx, sy, _ = w.project(cand)
                    shot = (sx, sy)
        elif inp.fire and self.cool <= 0:
            self.cool = C.SHOT_COOLDOWN
            shot = (inp.fire_x, inp.fire_y)
        if shot is not None:
            a.audio.play("shoot", 0.6)
            fx.tracer(C.CX, C.H + 10, shot[0], shot[1])
            for h in w.fire(*shot):
                if h.killed:
                    fx.explosion(h.sx, h.sy, h.target.color, 1.4, big=True)
                    fx.add_shake(8)
                    a.audio.play("explode", 0.7)
                    a.audio.play_hit(4)
                    self.phase = "nice"
                    self.nice_t = 1.1

    def draw(self, dst):
        a, w = self.app, self.world
        now = a.clock_t
        a.tunnel.set_theme(a.base_theme())
        a.tunnel.draw(dst, w.scroll, 0.45, dt=1 / 60)
        locked = None
        if self.cube is not None and self.cube.alive:
            sx, sy, sr = w.project(self.cube)
            if math.hypot(self.rx - sx, self.ry - sy) < max(sr * 1.6, 40):
                locked = self.cube
        for t in sorted(w.targets, key=lambda t: -t.z):
            tx, ty, tr = w.project(t)
            if t is self.cube and t.alive and self.phase != "nice":
                p = 0.5 + 0.5 * math.sin(now * 6)          # "look here" marker
                pygame.draw.circle(dst, scale_color(C.GOLD, 0.45 + 0.5 * p), (int(tx), int(ty)),
                                   int(max(tr * 2.0, 34) + 5 * p), 3)
            draw_target(dst, t, tx, ty, tr, now, locked=(t is locked))
        self.fx.draw(dst)
        easy = a.cfg.autofire
        verb = gspec(a)["verb"]
        titles = {
            "aim": ("AIM AT THE CUBE", "Move your hand - the reticle follows"),
            "shoot": (("HOLD STEADY" if easy else "NOW SHOOT!"),
                      ("Keep the reticle on the cube - it fires by itself" if easy else f"{verb} to fire")),
            "nice": ("NICE SHOT!", "Get ready..."),
        }
        title, hint = titles[self.phase]
        draw_hud_panel(dst, (C.CX - 320, 18, 640, 108), C.GREEN if self.phase == "nice" else C.CYAN)
        draw_text(dst, title, 46, C.GREEN if self.phase == "nice" else C.CYAN, (C.CX, 54))
        draw_text(dst, hint, 22, C.WHITE, (C.CX, 100), glow=False)
        frac = max(0.0, 1.0 - self.t / C.TRIAL_TIMEOUT)
        draw_bar(dst, C.CX - 300, 132, 600, 6, frac, C.GREY)
        self.skip_btn.draw(dst, now)
        draw_reticle(dst, self.rx, self.ry, now, False, locked is not None, 0, False, False)

    def draw_overlay(self, dst):
        fx = self.fx
        if fx.flash > 0.02:
            v = self.app.tunnel.vign
            v.set_alpha(int(255 * min(1.0, fx.flash)))
            dst.blit(v, (0, 0))


# =============================================================== CALIBRATE
class CalibrateScene(Scene):
    SETTLE = 0.7
    COLLECT = 1.6

    def __init__(self, app, then_menu=True):
        super().__init__(app)
        self.then_menu = then_menu
        self.step = 0                 # 0 open, 1 fist, 2 done
        self.t = 0.0
        self.samples: list[float] = []
        self.results: list[float] = []
        self.message = ""
        self.done_t = 0.0
        self.ok = True

    def update(self, dt, inp):
        super().update(dt, inp)
        a = self.app
        if self.step in (0, 1):
            spec = gspec(a)
            if self.step == 0:
                pose_ok = inp.ratio > spec["open_min"]
                hint = "open your hand wider - spread your fingers"
            else:
                pose_ok = inp.ratio < self.results[0] - spec["min_sep"]
                hint = ("pinch your thumb and index tighter" if a.cfg.gesture == "pinch"
                        else "close your fist tighter")
            if inp.present and pose_ok:
                self.t += dt
            else:
                self.t = 0.0
                self.samples.clear()
            if not inp.present:
                dark = getattr(a.input, "brightness", 255) < 40
                self.message = ("too dark - turn on a light / uncover the camera" if dark
                                else "I can't see your hand")
            elif not pose_ok:
                self.message = hint
            elif self.t > self.SETTLE:
                self.samples.append(inp.ratio)
                self.message = "hold it..."
            else:
                self.message = ""
            if len(self.samples) > 5 and self.t > self.SETTLE + self.COLLECT:
                self.results.append(statistics.median(self.samples))
                self.samples.clear()
                self.t = 0.0
                self.step += 1
                a.audio.play("tick")
                if self.step == 2:
                    o, c = self.results
                    self.ok = a.input.set_calibration(o, c)
                    if self.ok:
                        a.save_calibration(a.cfg.gesture, o, c)
                        a.audio.play("level_clear")
                    else:
                        a.audio.play("leak")
        else:
            self.done_t += dt
            if self.done_t > 2.2:
                if self.ok:
                    a.goto(MenuScene(a))
                else:
                    a.goto(CalibrateScene(a, self.then_menu))

    def on_key(self, key):
        if key == pygame.K_ESCAPE:
            self.app.goto(MenuScene(self.app))

    def draw(self, dst):
        a = self.app
        now = a.clock_t
        a.tunnel.set_theme(a.base_theme())
        a.tunnel.draw(dst, now * 6, 0.4, dt=1 / 60)
        draw_text(dst, f"HAND CALIBRATION - {gspec(a)['label']}", 60, C.CYAN, (C.CX, 80))
        prev = a.preview_surface(1.5)
        if prev is not None:
            r = prev.get_rect(center=(C.CX - 250, C.CY + 40))
            dst.blit(prev, r)
            pygame.draw.rect(dst, C.CYAN, r, 2)
        spec = gspec(a)
        titles = ["OPEN YOUR HAND", "MAKE A FIST" if a.cfg.gesture == "fist" else "PINCH FINGERS", "DONE"]
        subs = ["Palm toward the camera, fingers spread", spec["tip"], ""]
        s = min(self.step, 2)
        if self.step < 2:
            draw_text(dst, titles[s], 54, C.GREEN if s == 0 else C.ORANGE, (C.CX + 270, C.CY - 90))
            draw_text(dst, subs[s], 22, C.WHITE, (C.CX + 270, C.CY - 40), glow=False)
            frac = 0.0
            if self.t > self.SETTLE:
                frac = min(1.0, (self.t - self.SETTLE) / self.COLLECT)
            draw_bar(dst, C.CX + 130, C.CY + 10, 280, 16, frac, C.GREEN)
            draw_text(dst, self.message, 24, C.ORANGE, (C.CX + 270, C.CY + 56), glow=False)
            draw_text(dst, f"step {s + 1} of 2", 18, C.GREY, (C.CX + 270, C.CY + 100), glow=False)
        else:
            if self.ok:
                o, c = self.results
                draw_text(dst, "CALIBRATED", 58, C.GREEN, (C.CX + 270, C.CY - 70))
                draw_text(dst, f"open {o:.2f}   {spec['label'].lower()} {c:.2f}", 24, C.WHITE,
                          (C.CX + 270, C.CY - 10), glow=False)
            else:
                draw_text(dst, "TOO SIMILAR", 52, C.RED, (C.CX + 270, C.CY - 70))
                draw_text(dst, "Open wider / close tighter - trying again", 22, C.WHITE,
                          (C.CX + 270, C.CY - 10), glow=False)
        draw_text(dst, "Esc to skip", 18, C.GREY, (C.CX, C.H - 30), glow=False)


# =============================================================== TUTORIAL
class TutorialScene(Scene):
    """Learn by doing: aim into two rings, shoot three cubes, then shoot a cube but let a mine pass."""

    STEPS = ("aim", "shoot", "mine", "done")

    def __init__(self, app):
        super().__init__(app)
        self.rng = random.Random()
        self.world = World(self.rng)
        self.world.base_speed = 0.55
        self.fx = FX(self.rng)
        self.step = 0
        self.progress = 0.0
        self.count = 0
        self.zone = (C.CX - 330, C.CY - 40)
        self.zones_done = 0
        self.rx, self.ry = C.CX, C.CY
        self.lock_t = 0.0
        self.cool = 0.0
        self.message = ""
        self.msg_t = 0.0
        self.done_t = 0.0
        self.pair = []
        self.skip_btn = Button((24, C.H - 84, 190, 58), "SKIP", "skip", C.GREY, size=24)

    def enter(self):
        self.app.audio.sfx_muted = False
        self.app.audio.music_start()
        self.app.audio.set_intensity(0.15)

    # ---- helpers
    def _say(self, text, t=1.6):
        self.message, self.msg_t = text, t

    def _spawn_cube(self, kind="standard", side=None):
        x = self.rng.choice((-1, 1)) * self.rng.uniform(0.5, 1.4) if side is None else side
        return self.world.spawn(kind, x, self.rng.uniform(-0.5, 0.5), 30.0)

    def _finish(self):
        a = self.app
        a.cfg.tutorial_done = True
        a.cfg.save()
        a.goto(MenuScene(a))

    def on_key(self, key):
        if key == pygame.K_ESCAPE:
            self._finish()

    # ---- update
    def update(self, dt, inp):
        super().update(dt, inp)
        a, w, fx = self.app, self.world, self.fx
        self.rx, self.ry = inp.x, inp.y
        self.cool -= dt
        self.msg_t = max(0.0, self.msg_t - dt)
        fx.update(dt)
        w.update(dt)
        if self.skip_btn.update(dt, inp, locked=self.age < 0.8):
            a.audio.play("click")
            self._finish()
            return
        name = self.STEPS[self.step]
        auto = a.cfg.autofire

        def try_shot():
            """Manual fire or easy-mode auto-lock; returns the hits list (maybe empty)."""
            if auto:
                cand = w.lock_candidate(self.rx, self.ry)
                if cand is None:
                    self.lock_t = 0.0
                    return None
                self.lock_t += dt
                sx, sy, _ = w.project(cand)
                if self.lock_t >= C.AUTO_LOCK_TIME and self.cool <= 0:
                    self.cool = C.AUTO_FIRE_INTERVAL
                    return (sx, sy)
                return None
            if inp.fire and self.cool <= 0:
                self.cool = C.SHOT_COOLDOWN
                return (inp.fire_x, inp.fire_y)
            return None

        if name == "aim":
            zx, zy = self.zone
            if inp.present and math.hypot(self.rx - zx, self.ry - zy) < 85:
                self.progress += dt / 0.9
            else:
                self.progress = max(0.0, self.progress - dt * 2)
            if self.progress >= 1.0:
                self.progress = 0.0
                self.zones_done += 1
                a.audio.play("powerup", 0.6)
                fx.explosion(zx, zy, C.GREEN, 1.0)
                if self.zones_done >= 2:
                    self._next()
                else:
                    self.zone = (C.CX + 330, C.CY + 60)
        elif name in ("shoot", "mine"):
            solved = name == "mine" and self.count == 99      # cube shot; waiting for the mine to pass
            if solved and not any(t.kind == "mine" for t in w.targets):
                self._next()
                return
            if name == "shoot" and not w.targets:
                self._spawn_cube()
            if name == "mine" and not w.targets and not solved:
                side = self.rng.choice((-1, 1))
                self.pair = [self._spawn_cube("mine", side * 0.9), self._spawn_cube("standard", -side * 0.9)]
            shot = try_shot()
            if shot is not None:
                px, py = shot
                a.audio.play("shoot", 0.6)
                fx.tracer(C.CX, C.H + 10, px, py)
                for h in w.fire(px, py):
                    t = h.target
                    if t.hazard:
                        fx.explosion(h.sx, h.sy, C.RED, 1.4, big=True)
                        fx.add_shake(14)
                        fx.flash = 1.0
                        a.audio.play("mine")
                        self._say("OOPS - that was a mine! It would cost a life. Try again.", 2.4)
                        for o in w.targets:
                            o.alive = False
                        w.targets = []
                    elif h.killed:
                        fx.explosion(h.sx, h.sy, t.color, 1.2)
                        a.audio.play("explode", 0.6)
                        a.audio.play_hit(min(9, self.count * 2))
                        self.count += 1
                        if name == "shoot" and self.count >= 3:
                            self._next()
                        elif name == "mine":
                            self._say("Nice - mines are safe if you just let them pass.", 2.0)
                            self.done_t = 0.0
                            self.count = 99          # mark success; mine will drift by
        elif name == "done":
            self.done_t += dt
            if self.done_t > 2.6:
                self._finish()
                return

    def _next(self):
        self.step += 1
        self.count = 0
        self.progress = 0.0
        self.lock_t = 0.0
        for o in self.world.targets:
            o.alive = False
        self.world.targets = []
        self.app.audio.play("level_clear", 0.6)
        self.done_t = 0.0

    # ---- draw
    def draw(self, dst):
        a, w = self.app, self.world
        now = a.clock_t
        a.tunnel.set_theme(a.base_theme())
        a.tunnel.draw(dst, w.scroll, 0.5, dt=1 / 60)
        for t in sorted(w.targets, key=lambda t: -t.z):
            sx, sy, sr = w.project(t)
            draw_target(dst, t, sx, sy, sr, now, locked=False)
        self.fx.draw(dst)
        spec = gspec(a)
        verb = spec["verb"]
        name = self.STEPS[self.step]
        titles = {
            "aim": ("1 / 3   AIM", "Move your hand to put the reticle in the ring and hold it there"),
            "shoot": ("2 / 3   SHOOT", ("Rest the reticle on a cube - it fires by itself!" if a.cfg.autofire
                                        else f"Aim at a cube and {verb} to shoot it")),
            "mine": ("3 / 3   MINES", "Shoot the CYAN cube. Do NOT shoot the red spiky mine - let it pass"),
            "done": ("YOU'RE READY", "Go get a high score!"),
        }
        title, hint = titles[name]
        draw_hud_panel(dst, (C.CX - 400, 18, 800, 118), C.CYAN)
        draw_text(dst, title, 40, C.CYAN, (C.CX, 52))
        draw_text(dst, hint, 24, C.WHITE, (C.CX, 100), glow=False)
        if name == "aim":
            zx, zy = self.zone
            p = 0.6 + 0.4 * math.sin(now * 5)
            pygame.draw.circle(dst, scale_color(C.GREEN, 0.5 + 0.5 * p), (int(zx), int(zy)), 85, 3)
            add_glow(dst, zx, zy, C.GREEN, 130, 0.35)
            if self.progress > 0:
                rect = pygame.Rect(0, 0, 190, 190)
                rect.center = (int(zx), int(zy))
                pygame.draw.arc(dst, C.WHITE, rect, math.pi / 2, math.pi / 2 + math.tau * min(1.0, self.progress), 6)
        elif name == "shoot":
            draw_text(dst, f"{min(self.count, 3)} / 3", 34, C.GOLD, (C.CX, C.H - 70))
        if self.msg_t > 0:
            draw_text(dst, self.message, 30, C.ORANGE, (C.CX, C.CY + 170), fade=min(1.0, self.msg_t * 2))
        if name == "done":
            draw_text(dst, "tip: hold your fist/pinch then open it for a charged blast", 20, C.GREY,
                      (C.CX, C.CY + 60), glow=False)
        self.skip_btn.draw(dst, now)
        draw_text(dst, "Esc = skip", 16, C.GREY, (C.W - 24, C.H - 30), "bottomright", glow=False)
        draw_reticle(dst, self.rx, self.ry, now, False, False, 0, False, False)

    def draw_overlay(self, dst):
        fx = self.fx
        if fx.flash > 0.02:
            v = self.app.tunnel.vign
            v.set_alpha(int(255 * min(1.0, fx.flash)))
            dst.blit(v, (0, 0))


# =============================================================== RESULTS
class ResultsScene(Scene):
    LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    NO_NAME = "---"

    @property
    def wants_text(self) -> bool:
        """True while the name field is open: letter keys must type, not trigger hotkeys."""
        return self.entering and not self.saved

    def __init__(self, app, stats: dict):
        super().__init__(app)
        self.s = stats
        self.fx = FX()
        self.table = stats.get("table", stats["mode"])
        self.entering = S.qualifies(self.table, stats["score"], stats["level"])
        self.name = ""
        self.saved = False
        self.saved_as = ""
        # on-screen keyboard: two rows of 13, then DEL / SPACE / SAVE / NO NAME
        self.letter_btns: list[Button] = []
        kx, ky, pitch = C.CX - 13 * 54 // 2, 494, 54
        for i, ch in enumerate(self.LETTERS):
            r, c = divmod(i, 13)
            self.letter_btns.append(Button((kx + c * pitch, ky + r * 54, 50, 46), ch, ch,
                                           C.CYAN, size=26, dwell=0.8))
        cy = ky + 2 * 54 + 8
        x0 = C.CX - 336
        self.del_btn = Button((x0, cy, 110, 56), "DEL", "del", C.ORANGE, size=24, dwell=0.8)
        self.space_btn = Button((x0 + 124, cy, 150, 56), "SPACE", "space", C.CYAN, size=22, dwell=0.8)
        self.ok_btn = Button((x0 + 288, cy, 170, 56), "SAVE", "ok", C.GREEN, size=26, dwell=0.8)
        self.noname_btn = Button((x0 + 472, cy, 200, 56), "NO NAME", "noname", C.GREY, size=22,
                                 dwell=0.8, sub="save as ---")
        self.final_btns = [
            Button((C.CX - 280, 590, 260, 64), "PLAY AGAIN", "again", C.CYAN, size=28),
            Button((C.CX + 20, 590, 260, 64), "MENU", "menu", C.MAGENTA, size=28),
        ]
        self.audio_done = False

    def enter(self):
        a = self.app
        a.audio.sfx_muted = False
        a.audio.set_intensity(0.2)
        good = self.s.get("celebrate", self.s["rank"] in "SAB")
        a.audio.play("level_clear" if good else "game_over", 0.7)

    # ---- name editing
    def _add(self, ch: str):
        if len(self.name) >= C.NAME_MAX:
            return False
        if ch == " " and (not self.name or self.name.endswith(" ")):
            return False                       # no leading / doubled spaces
        self.name += ch
        return True

    def _clean(self) -> str:
        return self.name.strip()

    def update(self, dt, inp):
        super().update(dt, inp)
        self.fx.update(dt)
        self._inp = inp
        a = self.app
        locked = self.age < 1.0
        if self.age > 1.4 and not self.audio_done:
            self.audio_done = True
            col = self.s.get("rank_color") or RANK_COLORS.get(self.s["rank"], C.GREY)
            self.fx.explosion(C.CX + 330, 250, col, 1.6, big=bool(self.s.get("celebrate", self.s["rank"] in "SA")))
            a.audio.play("explode", 0.6)
        if self.entering and not self.saved:
            for b in self.letter_btns:
                if b.update(dt, inp, locked) and self._add(b.action):
                    a.audio.play("click")
            if self.space_btn.update(dt, inp, locked) and self._add(" "):
                a.audio.play("click")
            self.ok_btn.enabled = bool(self._clean())
            if self.del_btn.update(dt, inp, locked) and self.name:
                self.name = self.name[:-1]
                a.audio.play("click")
            if self.ok_btn.update(dt, inp, locked) and self._clean():
                self._save(self._clean())
                a.audio.play("powerup")
            if self.noname_btn.update(dt, inp, locked):
                self._save(self.NO_NAME)
                a.audio.play("click")
            return
        for b in self.final_btns:
            if b.update(dt, inp, locked):
                a.audio.play("click")
                if b.action == "again":
                    again = self.s.get("again")
                    a.goto(again() if again else PlayScene(a, self.s["mode"]))
                else:
                    a.goto(MenuScene(a))
                return

    def _save(self, name: str):
        s = self.s
        S.save_score(self.table, name, s["score"], s["level"], s["accuracy"])
        self.saved = True
        self.saved_as = name

    def on_key(self, key):
        # keyboard entry is a convenience for development / people with a keyboard
        if self.entering and not self.saved:
            if pygame.K_a <= key <= pygame.K_z:
                self._add(chr(key).upper())
            elif pygame.K_0 <= key <= pygame.K_9:
                self._add(chr(key))
            elif key == pygame.K_SPACE:
                self._add(" ")
            elif key == pygame.K_BACKSPACE:
                self.name = self.name[:-1]
            elif key == pygame.K_RETURN:
                self._save(self._clean() or self.NO_NAME)

    def _draw_name_bar(self, dst, now):
        """A wide text field (up to NAME_MAX characters) with a blinking caret and a counter."""
        draw_text(dst, "NEW HIGH SCORE  -  ENTER YOUR NAME", 24, C.GOLD, (C.CX, 392), glow=False)
        draw_hud_panel(dst, (C.CX - 312, 408, 624, 74), C.GOLD)
        field = pygame.Rect(C.CX - 296, 417, 592, 56)
        pygame.draw.rect(dst, (8, 12, 32), field, border_radius=8)
        pygame.draw.rect(dst, C.CYAN, field, 2, border_radius=8)
        text = self.name
        avail = field.w - 100                       # leave room for the counter
        size = 40
        while size > 18 and text and fx_font(size).size(text)[0] > avail:
            size -= 2
        tx = field.left + 16
        if text:
            r = draw_text(dst, text, size, C.WHITE, (tx, field.centery), "midleft", glow=False)
            cx = (r.right if r else tx) + 4
        else:
            draw_text(dst, "type with the letters below", 20, (90, 105, 140), (tx, field.centery),
                      "midleft", glow=False)
            cx = tx
        if len(self.name) < C.NAME_MAX and int(now * 2.6) % 2 == 0:
            pygame.draw.line(dst, C.CYAN, (cx, field.top + 12), (cx, field.bottom - 12), 3)
        n = len(self.name)
        draw_text(dst, f"{n}/{C.NAME_MAX}", 20, C.ORANGE if n >= C.NAME_MAX else C.GREY,
                  (field.right - 14, field.centery), "midright", glow=False)

    def draw(self, dst):
        a = self.app
        now = a.clock_t
        s = self.s
        a.tunnel.set_theme(a.base_theme())
        a.tunnel.draw(dst, now * 5, 0.3, dt=1 / 60)
        veil = pygame.Surface((C.W, C.H), pygame.SRCALPHA)
        veil.fill((2, 3, 14, 120))
        dst.blit(veil, (0, 0))
        title = s.get("title") or ("TIME!" if s["mode"] == "timed" else "SYSTEM FAILURE")
        tcol = s.get("title_color") or (C.GOLD if s["mode"] in ("timed", "versus", "coop", "duel") else C.RED)
        draw_text(dst, title, 70 if len(title) < 16 else 58, tcol, (C.CX, 62))
        if s.get("easy"):
            draw_text(dst, "EASY MODE - AUTO FIRE", 20, C.GREEN, (C.CX, 112), glow=False)
        draw_hud_panel(dst, (C.CX - 520, 130, 640, 244), C.CYAN)
        rows = [("SCORE", f"{s['score']:,}"),
                ("ACCURACY", f"{int(s['accuracy'] * 100)}%"),
                ("BEST COMBO", f"{s['max_combo']}"),
                ("TARGETS", f"{s['kills']}")]
        if s["mode"] == "endless":
            rows.insert(1, ("LEVEL REACHED", f"{s['level']}"))
        else:
            rows.append(("MISSED", f"{s['leaks']}"))
        row_cols = {"SCORE": C.GOLD, "ACCURACY": C.GREEN, "BEST COMBO": C.ORANGE, "TARGETS": C.CYAN,
                    "MISSED": C.RED, "LEVEL REACHED": C.MAGENTA}
        if s.get("rows"):                                   # 2-player screens supply their own rows
            rows = [(k, v) for k, v, _c in s["rows"]]
            row_cols = {k: c for k, _v, c in s["rows"]}
        for i, (k, v) in enumerate(rows):
            y = 148 + i * 43
            reveal = min(1.0, max(0.0, (self.age - 0.15 * i) * 4))
            rc = row_cols.get(k, C.WHITE)
            draw_text(dst, k, 24, scale_color(rc, 0.8), (C.CX - 480, y + 4), "topleft", glow=False, fade=reveal)
            draw_text(dst, v, 34, rc, (C.CX + 80, y), "topright", fade=reveal)
        # rank
        if self.age > 1.2:
            col = s.get("rank_color") or RANK_COLORS.get(s["rank"], C.GREY)
            k = min(1.0, (self.age - 1.2) * 4)
            draw_text(dst, "WINNER" if s.get("rank_color") else "RANK", 22, C.GREY, (C.CX + 330, 150), glow=False)
            base = 170 if len(s["rank"]) <= 1 else 120
            draw_text(dst, s["rank"], int(base * (0.6 + 0.4 * k)), col, (C.CX + 330, 262), fade=k)
        self.fx.draw(dst)

        if self.entering and not self.saved:
            self._draw_name_bar(dst, now)
            for b in self.letter_btns + [self.del_btn, self.space_btn, self.ok_btn, self.noname_btn]:
                b.draw(dst, now)
        else:
            if self.entering and self.saved:
                who = "NO NAME" if self.saved_as == self.NO_NAME else self.saved_as
                draw_text(dst, f"SCORE SAVED  -  {who}", 30, C.GREEN, (C.CX, 480))
            else:
                draw_text(dst, "no high score this time - try again!", 24, C.GREY, (C.CX, 480), glow=False)
            for b in self.final_btns:
                b.draw(dst, now)
        inp = getattr(self, "_inp", None)
        if inp is not None:
            draw_reticle(dst, inp.x, inp.y, now, inp.fist, False, 0, False, False, present=inp.present)


# =============================================================== HIGH SCORES
class ScoresScene(Scene):
    def __init__(self, app):
        super().__init__(app)
        self.back = Button((C.CX - 440, C.H - 100, 200, 60), "BACK", "back", C.CYAN, size=28)
        self.easy = bool(app.cfg.autofire)
        self.pair = False                       # False: solo tables, True: 2-player tables
        self.tab = Button((C.CX - 220, C.H - 100, 290, 60), "", "tab", C.GREEN, size=22)
        self.who = Button((C.CX + 90, C.H - 100, 330, 60), "", "who", C.MAGENTA, size=22)
        self._sync_tab()

    def _sync_tab(self):
        self.tab.label = "SHOWING: EASY (AUTO-FIRE)" if self.easy else "SHOWING: NORMAL"
        self.tab.color = C.GREEN if self.easy else C.CYAN
        self.tab.enabled = not self.pair
        self.who.label = "SHOWING: 2 PLAYER" if self.pair else "SHOWING: SOLO"
        self.who.color = C.MAGENTA if self.pair else C.GOLD
        self.who.enabled = C.TWO_PLAYER

    def update(self, dt, inp):
        super().update(dt, inp)
        self._inp = inp
        locked = self.age < C.UI_LOCK_TIME
        if self.back.update(dt, inp, locked):
            self.app.audio.play("click")
            self.app.goto(MenuScene(self.app))
        elif self.tab.update(dt, inp, locked):
            self.app.audio.play("click")
            self.easy = not self.easy
            self._sync_tab()
        elif self.who.update(dt, inp, locked):
            self.app.audio.play("click")
            self.pair = not self.pair
            self._sync_tab()

    def on_key(self, key):
        if key == pygame.K_ESCAPE:
            self.app.goto(MenuScene(self.app))

    def draw(self, dst):
        a = self.app
        now = a.clock_t
        a.tunnel.set_theme(a.base_theme())
        a.tunnel.draw(dst, now * 5, 0.3, dt=1 / 60)
        if self.pair:
            heading = "HIGH SCORES  -  2 PLAYER"
            panels = (("versus", "VERSUS", C.CYAN), ("coop", "CO-OP", C.GREEN), ("duel", "DUEL", C.GOLD))
            suffix = ""
        else:
            heading = "HIGH SCORES" + ("  -  EASY" if self.easy else "")
            panels = (("timed", "SCORE ATTACK", C.CYAN), ("endless", "SURVIVAL", C.MAGENTA))
            suffix = "_easy" if self.easy else ""
        draw_text(dst, heading, 70, C.GOLD, (C.CX, 70))
        n = len(panels)
        pw = 560 if n == 2 else 380
        gap = 30 if n == 2 else 20
        left = C.CX - (n * pw + (n - 1) * gap) // 2
        for col, (mode, title, colr) in enumerate(panels):
            x0 = left + col * (pw + gap)
            draw_panel(dst, (x0, 130, pw, 460), border=colr)
            draw_text(dst, title, 34, colr, (x0 + pw // 2, 165))
            rows = S.top_scores(mode + suffix, 10)
            if not rows:
                draw_text(dst, "no scores yet", 24, C.GREY, (x0 + pw // 2, 330), glow=False)
            for i, e in enumerate(rows):
                y = 205 + i * 37
                medal = (C.GOLD, (215, 225, 240), (232, 150, 92)).__getitem__(i) if i < 3 else C.GREY
                draw_text(dst, f"{i + 1:>2}.", 22, medal, (x0 + 14, y), "topleft", glow=False)
                # names can be up to 20 characters: shrink to fit the name column
                name = e.get("name", "???")
                col_w = pw - 62 - 110 - (70 if mode == "endless" else 0)
                nsz = 24
                while nsz > 14 and fx_font(nsz).size(name)[0] > col_w:
                    nsz -= 2
                draw_text(dst, name, nsz, C.WHITE, (x0 + 54, y + (24 - nsz) // 2), "topleft", glow=False)
                if mode == "endless":
                    draw_text(dst, f"LVL {e.get('level', 0)}", 20, colr, (x0 + pw - 170, y + 2), "topleft",
                              glow=False)
                draw_text(dst, f"{e.get('score', 0):,}", 24, C.GOLD, (x0 + pw - 20, y), "topright", glow=False)
        self.back.draw(dst, now)
        self.tab.draw(dst, now)
        self.who.draw(dst, now)
        inp = getattr(self, "_inp", None)
        if inp is not None:
            draw_reticle(dst, inp.x, inp.y, now, inp.fist, False, 0, False, False, present=inp.present)
