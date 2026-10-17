"""Headless end-to-end smoke test: drives the real App loop with scripted input,
walks Menu -> Timed run -> Results and Endless levels, and saves screenshots.

    python tests/smoke.py <output_dir>
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pygame  # noqa: E402

from neural import config as C  # noqa: E402
from neural.app import App  # noqa: E402
from neural.inputs import BotInput, InputProvider, InputState  # noqa: E402
from neural.scenes import (MenuScene, PickScene, PlayScene, ResultsScene,  # noqa: E402
                           SettingsScene, TrialScene, TutorialScene)

import math  # noqa: E402
import tempfile  # noqa: E402

from neural import scoring as S  # noqa: E402

_tmp = Path(tempfile.mkdtemp())            # never touch the player's real scores/settings
C.DATA_DIR, C.SCORES_PATH, C.CALIB_PATH = _tmp, _tmp / "scores.json", _tmp / "calibration.json"
from neural.app import Settings  # noqa: E402
Settings.PATH = _tmp / "settings.json"

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
OUT.mkdir(parents=True, exist_ok=True)
DT = 1 / 60


class Scripted(InputProvider):
    name = "script"
    has_camera = False

    def __init__(self):
        self.x, self.y, self.fire = C.CX, C.CY, False

    def poll(self, dt):
        s = InputState(x=self.x, y=self.y, fire=self.fire, fist=self.fire,
                       fire_x=self.x, fire_y=self.y)
        self.fire = False
        return s


def step(app, n=1):
    for _ in range(n):
        app.clock_t += DT
        inp = app.input.poll(DT)
        app._last_inp = inp
        if inp.present:
            app._last_hand = app.clock_t
        app._check_idle()
        app.scene.update(DT, inp)
        app._swap()
        app.audio.update(DT)
        app._draw()


def shot(app, name):
    pygame.image.save(app.screen, str(OUT / f"{name}.png"))
    print("saved", name, type(app.scene).__name__)


def main():
    t0 = time.time()
    app = App(input_mode="mouse", audio=True, skip_calibration=True)
    print(f"app built in {time.time() - t0:.1f}s; audio enabled={app.audio.enabled}")
    script = Scripted()
    app.input = script
    assert isinstance(app.scene, MenuScene)
    step(app, 90)
    shot(app, "01_menu")

    # --- WAVE TO START: hold the hand in the big PLAY circle -> pick screen; either/or choices work
    script.has_camera = True                     # behave like a camera input for the flow
    app.cfg.autofire = False
    home = app.scene
    script.x, script.y = home.play_btn.cx, home.play_btn.cy
    step(app, 100)                               # hold ~1.7 s (UI lock 0.8 s + dwell 1.0 s)
    assert isinstance(app.scene, PickScene), app.scene
    step(app, 40)
    shot(app, "01c_pick")

    def press(btn):
        step(app, 50)                            # let the UI lock / hover settle
        script.x, script.y = btn.rect.center
        step(app, 3)
        script.fire = True
        step(app, 3)

    pick = app.scene
    press(pick.diff[0])
    assert app.cfg.autofire and pick.diff[0].selected and not pick.diff[1].selected
    press(pick.diff[1])
    assert not app.cfg.autofire and pick.diff[1].selected
    press(pick.fire[1])
    assert app.cfg.gesture == "pinch" and pick.fire[1].selected
    shot(app, "01d_pick_pinch")
    press(pick.fire[0])
    assert app.cfg.gesture == "fist"
    press(pick.back)
    assert isinstance(app.scene, MenuScene), app.scene
    script.has_camera = False
    script.x, script.y = C.CX, C.CY

    # --- tutorial: aim into two rings, shoot 3 cubes, then shoot the cube but not the mine
    app.cfg.autofire = False
    app.goto(TutorialScene(app))
    step(app, 2)
    tut = app.scene
    app.input = script
    for _ in range(2):                                   # step 1: hold the reticle in the ring
        script.x, script.y = tut.zone
        step(app, 70)
    assert tut.STEPS[tut.step] == "shoot", tut.STEPS[tut.step]
    shot(app, "02b_tutorial_shoot")
    guard = 0
    while tut.STEPS[tut.step] == "shoot" and guard < 60 * 40:
        for t in tut.world.targets:
            sx, sy, _ = tut.world.project(t)
            if t.z < 26:
                script.x, script.y = sx, sy
                script.fire = True
        step(app, 1)
        guard += 1
    assert tut.STEPS[tut.step] == "mine", tut.STEPS[tut.step]
    step(app, 30)
    shot(app, "02c_tutorial_mines")
    guard = 0
    while tut.STEPS[tut.step] == "mine" and guard < 60 * 40:
        for t in tut.world.targets:
            if t.kind == "standard" and t.z < 26:        # shoot only the cube, never the mine
                sx, sy, _ = tut.world.project(t)
                script.x, script.y = sx, sy
                script.fire = True
        step(app, 1)
        guard += 1
    assert tut.STEPS[tut.step] == "done", tut.STEPS[tut.step]
    step(app, 60 * 3)
    assert isinstance(app.scene, MenuScene) and app.cfg.tutorial_done
    print("tutorial completed")
    script.x, script.y = C.CX, C.CY

    # --- real walk-up flow: PLAY circle -> Score Attack card -> 10-second trial -> game
    script.has_camera = True
    app.session_trial_done = False
    app.goto(MenuScene(app))
    step(app, 5)
    script.x, script.y = app.scene.play_btn.cx, app.scene.play_btn.cy
    step(app, 150)                               # fresh screen: 0.8 s UI lock + 1.0 s hold
    assert isinstance(app.scene, PickScene), app.scene
    press(app.scene.modes[0])
    assert isinstance(app.scene, TrialScene), app.scene
    trial = app.scene
    step(app, 30)
    shot(app, "01e_trial")
    t_start = app.clock_t
    guard = 0
    while isinstance(app.scene, TrialScene) and guard < 60 * 20:
        for t in trial.world.targets:
            sx, sy, _ = trial.world.project(t)
            script.x, script.y = sx, sy
            script.fire = True
        step(app, 1)
        guard += 1
    assert isinstance(app.scene, PlayScene) and app.scene.mode_key == "timed", app.scene
    assert app.session_trial_done
    print("trial took %.1f s (limit %.0f s)" % (app.clock_t - t_start, C.TRIAL_TIMEOUT))
    assert app.clock_t - t_start <= C.TRIAL_TIMEOUT + 3
    script.has_camera = False
    play = app.scene
    step(app, 100)
    shot(app, "02_timed_countdown")

    app.input = BotInput(play.world, skill=0.9)
    step(app, 5 * 60)                       # past the countdown
    shot(app, "03_timed_start")
    step(app, 25 * 60)
    shot(app, "04_timed_mid")
    step(app, 18 * 60)
    shot(app, "05_timed_late")
    frames = 0
    while isinstance(app.scene, PlayScene) and frames < 60 * 40:
        step(app, 1)
        frames += 1
        if frames == 5 * 60:
            shot(app, "06_timed_end")
    assert isinstance(app.scene, ResultsScene), app.scene
    step(app, 120)
    res = app.scene
    assert res.entering
    res.on_key(pygame.K_a)
    res.on_key(pygame.K_d)
    step(app, 20)
    shot(app, "07_results_name_bar")
    print("timed score", res.s["score"], "rank", res.s["rank"], "entering initials:", res.entering)
    # NO NAME button (operated by the reticle like every other button)
    app.input = script
    script.x, script.y = res.noname_btn.rect.center
    step(app, 2)
    script.fire = True
    step(app, 2)
    assert res.saved and res.saved_as == "---", (res.saved, res.saved_as)
    assert S.top_scores("timed")[0]["name"] == "---"
    step(app, 10)
    shot(app, "07b_results_saved_no_name")

    # --- easy mode: auto-fire fires for you, but AIMING is the skill. The bot never fires.
    class AimOnly(BotInput):
        """kind 'steady': aims at targets with a small tremor; 'wave': flails around, no aiming."""

        def __init__(self, world, kind, **k):
            super().__init__(world, **k)
            self.kind = kind
            self.t = 0.0

        def poll(self, dt):
            self.t += dt
            if self.kind == "wave":
                return InputState(x=C.CX + 420 * math.cos(self.t * 3.3),
                                  y=C.CY + 220 * math.sin(self.t * 2.1), present=True)
            st = super().poll(dt)
            st.fire = False
            st.fist = False
            st.x += 8 * math.cos(self.t * 3.1)
            st.y += 8 * math.sin(self.t * 2.3)
            return st

    results = {}
    for kind in ("steady", "wave"):
        app.cfg.autofire = True
        app.input = script
        app.goto(PlayScene(app, "timed"))
        step(app, 2)
        play = app.scene
        play.rng.seed(11)
        assert play.autofire and play.table == "timed_easy", (play.autofire, play.table)
        app.input = AimOnly(play.world, kind, skill=1.0)
        step(app, 5 * 60 + 30 * 60)
        if kind == "steady":
            shot(app, "11_easy_mode")
        sc = play.scoring
        results[kind] = sc.kills
        print(f"easy mode, {kind:6s} hand: kills {sc.kills}  shots {sc.shots}  missed {sc.leaks}"
              f"  mines hit {sc.mines_hit}")
        assert sc.mines_hit == 0, "auto-fire must never shoot a mine"
    assert results["steady"] >= 10, "a steady aimer must be able to play easy mode"
    assert results["wave"] <= max(2, results["steady"] // 5),         "waving the hand around must not clear the screen in easy mode"
    app.cfg.autofire = False

    # --- endless
    app.input = script
    app.goto(PlayScene(app, "endless"))
    step(app, 2)
    play = app.scene
    app.input = BotInput(play.world, skill=0.7)
    step(app, 4 * 60)
    shot(app, "08_endless_l1")
    seen_clear = False
    for i in range(60 * 150):
        step(app, 1)
        if play.mode.phase == "clear" and not seen_clear:
            step(app, 30)
            shot(app, "09_endless_level_clear")
            seen_clear = True
        if play.mode.level >= 5 and play.mode.phase == "play" and play.mode.boss_spawned:
            step(app, 120)
            shot(app, "10_endless_miniboss")
            break
        if not isinstance(app.scene, PlayScene):
            break
    print("endless level", play.mode.level, "lives", play.mode.lives,
          "score", play.scoring.score)
    # --- settings screen
    app.input = script
    script.x, script.y = C.CX, 20
    app.goto(MenuScene(app))
    step(app, 5)
    app.goto(SettingsScene(app))
    step(app, 60)
    shot(app, "12_settings")
    fs0 = app.cfg.fullscreen
    pv0 = app.show_pip
    snd0 = app.audio.muted
    st = app.scene
    script.has_camera = True
    st._sync()
    press(st.buttons[2])                         # sound
    assert app.audio.muted != snd0
    press(st.buttons[2])
    press(st.buttons[1])                         # camera view
    assert app.show_pip != pv0 and app.cfg.show_preview == app.show_pip,         (pv0, app.show_pip, app.cfg.show_preview, type(app.scene).__name__, [b.enabled for b in st.buttons])
    press(st.buttons[1])
    script.has_camera = False

    # --- AUTO-RESET AFTER IDLE: nobody in front of the camera for 15 s -> back to the attract screen
    class Nobody(InputProvider):
        name = "nobody"
        has_camera = True

        def poll(self, dt):
            return InputState(present=False)

    app.goto(PlayScene(app, "endless"))
    step(app, 5)
    app.input = Nobody()
    app._last_hand = app.clock_t
    app.session_trial_done = True
    step(app, int((C.IDLE_RESET - 3) * 60))
    assert isinstance(app.scene, PlayScene), "must not reset before the idle limit"
    step(app, int(5 * 60))
    assert isinstance(app.scene, MenuScene), app.scene
    assert app.session_trial_done is False, "the next visitor must get the trial again"
    print("idle reset OK")
    print("OK total %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
