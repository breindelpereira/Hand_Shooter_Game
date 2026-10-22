"""Headless screenshots of the menu screens (to review UI changes).
    python tests/ui_shots.py <output_dir>
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pygame  # noqa: E402

from neural import config as C  # noqa: E402

_tmp = Path(tempfile.mkdtemp())
C.DATA_DIR, C.SCORES_PATH, C.CALIB_PATH, C.STATS_PATH = (
    _tmp, _tmp / "scores.json", _tmp / "calibration.json", _tmp / "stats.json")
from neural import scenes as SC  # noqa: E402
from neural import scoring as S  # noqa: E402
from neural.app import App, Settings  # noqa: E402
from neural.inputs import InputState  # noqa: E402

Settings.PATH = _tmp / "settings.json"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
OUT.mkdir(parents=True, exist_ok=True)
DT = 1 / 60

app = App(input_mode="mouse", audio=False, skip_calibration=True, fullscreen=False)
for i, (n, sc) in enumerate((("Ava", 52000), ("Ben", 41000), ("Cy", 30500), ("Dee", 22000))):
    S.save_score("timed", n, sc, 0, 0.7)


def shot(name, scene, seconds=1.6, mouse=(900, 420)):
    app.goto(scene)
    app._swap()
    for _ in range(int(seconds / DT)):
        app.clock_t += DT
        app.scene.update(DT, InputState(x=mouse[0], y=mouse[1], present=True))
        app._swap()
        app._draw()
    pygame.image.save(app.screen, str(OUT / f"{name}.png"))


shot("menu", SC.MenuScene(app), 2.0)
shot("pick", SC.PickScene(app))
app.want_players = 2
shot("pick2p", SC.PickScene(app))
shot("scores", SC.ScoresScene(app))
r = SC.ResultsScene(app, dict(mode="timed", score=48200, accuracy=0.74, max_combo=17, kills=63, leaks=4,
                              rank="A", easy=False, table="timed", level=1))
shot("results", r, 2.4)
print("saved", OUT)
