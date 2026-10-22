"""Monkey test: a bot wanders and clicks randomly through the whole game to find crashes.

    python tests/monkey.py [frames] [seed]

It runs with player 2 present too, both with and without a (fake) camera, and reports every
scene type it visited plus any exception.
"""
import os
import random
import sys
import tempfile
import traceback
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neural import config as C  # noqa: E402

_tmp = Path(tempfile.mkdtemp())
C.DATA_DIR, C.SCORES_PATH, C.CALIB_PATH, C.STATS_PATH = (
    _tmp, _tmp / "scores.json", _tmp / "calibration.json", _tmp / "stats.json")
from neural.app import App, Settings  # noqa: E402
Settings.PATH = _tmp / "settings.json"
from neural.inputs import InputProvider, InputState  # noqa: E402

DT = 1 / 60


class Monkey(InputProvider):
    name = "monkey"

    def __init__(self, camera: bool, rng):
        self.has_camera = camera
        self.rng = rng
        self.pos = [[C.CX, C.CY], [C.CX + 200, C.CY]]
        self.goal = [[C.CX, C.CY], [C.CX, C.CY]]
        self.hold = [0, 0]

    def _one(self, i, dt):
        r = self.rng
        if self.hold[i] <= 0:
            self.goal[i] = [r.uniform(0, C.W), r.uniform(0, C.H)]
            self.hold[i] = r.randint(20, 150)
        self.hold[i] -= 1
        p, g = self.pos[i], self.goal[i]
        k = 1 - 2.7 ** (-dt * 9)
        p[0] += (g[0] - p[0]) * k
        p[1] += (g[1] - p[1]) * k
        fire = r.random() < 0.03
        return InputState(x=p[0], y=p[1], present=True, fist=fire, fire=fire, fire_x=p[0], fire_y=p[1])

    def poll(self, dt):
        return self._one(0, dt)

    def poll_players(self, dt):
        return [self._one(0, dt), self._one(1, dt) if self.players == 2 else InputState(present=False)]


def run(frames, seed, camera):
    rng = random.Random(seed)
    app = App(input_mode="mouse", audio=False, skip_calibration=True, fullscreen=False)
    app.input = Monkey(camera, rng)
    visited, errors = {}, []
    scene_age = 0
    for i in range(frames):
        app.clock_t += DT
        inps = app.input.poll_players(DT)
        app.inp2 = inps[1]
        app._last_inp = inps[0]
        app._last_hand = app.clock_t
        try:
            app.scene.update(DT, inps[0])
            app._swap()
            if i % 3 == 0:
                app._draw()
        except Exception:                      # noqa: BLE001
            errors.append((type(app.scene).__name__, traceback.format_exc()))
            app.goto(type(app.scene)(app) if False else __import__("neural.scenes", fromlist=["MenuScene"]).MenuScene(app))
            app._swap()
        name = type(app.scene).__name__
        visited[name] = visited.get(name, 0) + 1
        scene_age += 1
        if rng.random() < 0.002:               # occasionally flip choices the menus offer
            app.want_players = rng.choice((1, 2))
            app.cfg.autofire = rng.random() < 0.5
            app.cfg.gesture = rng.choice(("fist", "pinch"))
        if rng.random() < 0.0012:                # jump into screens the monkey rarely finds by clicking
            from neural import scenes as SC
            from neural.versus import VersusScene
            factories = [lambda: SC.SettingsScene(app), lambda: SC.ScoresScene(app),
                         lambda: SC.TutorialScene(app), lambda: SC.CalibrateScene(app),
                         lambda: SC.TrialScene(app, lambda: SC.PlayScene(app, "endless")),
                         lambda: VersusScene(app, coop=rng.random() < 0.5),
                         lambda: SC.PlayScene(app, rng.choice(("timed", "endless")))]
            try:
                app.goto(rng.choice(factories)())
            except Exception:                    # noqa: BLE001
                errors.append(("scene factory", traceback.format_exc()))
        if rng.random() < 0.0015 and hasattr(app.scene, "on_key"):
            import pygame
            try:
                app._on_key(rng.choice((pygame.K_ESCAPE, pygame.K_p, pygame.K_a, pygame.K_c, pygame.K_m,
                                        pygame.K_BACKSPACE, pygame.K_RETURN, pygame.K_SPACE, pygame.K_F3)))
            except Exception:                  # noqa: BLE001
                errors.append((name + " (key)", traceback.format_exc()))
    return visited, errors


if __name__ == "__main__":
    frames = int(sys.argv[1]) if len(sys.argv) > 1 else 40000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    bad = 0
    for camera in (False, True):
        visited, errors = run(frames, seed + (10 if camera else 0), camera)
        print(f"camera={camera!s:5} frames={frames}  scenes visited: "
              + ", ".join(f"{k}:{v}" for k, v in sorted(visited.items())))
        seen = set()
        for where, tb in errors:
            key = tb.strip().splitlines()[-1]
            if key in seen:
                continue
            seen.add(key)
            bad += 1
            print(f"\n!! exception in {where}:\n{tb}")
    print("\nMONKEY RESULT:", "no exceptions" if not bad else f"{bad} distinct exception(s)")
    sys.exit(1 if bad else 0)
