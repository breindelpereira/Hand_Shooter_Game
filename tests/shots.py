"""Fast headless screenshots of gameplay at different moments / themes.
    python tests/shots.py <output_dir>
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pygame  # noqa: E402

from neural import config as C  # noqa: E402
from neural.app import App  # noqa: E402
from neural.inputs import BotInput  # noqa: E402
from neural.scenes import PlayScene  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
OUT.mkdir(parents=True, exist_ok=True)
DT = 1 / 60


def advance(app, frames, draw_last=True):
    for i in range(frames):
        app.clock_t += DT
        inp = app.input.poll(DT)
        app._last_inp = inp
        app.scene.update(DT, inp)
        app._swap()
        if i == frames - 1 and draw_last:
            app._draw()
        else:                               # keep scenery/theme blending alive without full draw
            sc = app.scene
            if isinstance(sc, PlayScene):
                sc.app.tunnel._blend(DT)
                sc.app.tunnel.set_theme(sc._theme_index())


def shot(app, name):
    pygame.image.save(app.screen, str(OUT / f"{name}.png"))
    print("saved", name)


app = App(input_mode="mouse", audio=False, skip_calibration=True)
advance(app, 5)
app.goto(PlayScene(app, "timed"))
advance(app, 2)
play = app.scene
app.input = BotInput(play.world, skill=0.9)
advance(app, 5 * 60 + 14 * 60)
shot(app, "s1_timed_start_theme0")
advance(app, 20 * 60)
shot(app, "s2_timed_mid_theme_void")
advance(app, 14 * 60)
shot(app, "s3_timed_late_theme_inferno")

app.cfg.autofire = False
app.goto(PlayScene(app, "endless"))
advance(app, 2)
play = app.scene
app.input = BotInput(play.world, skill=0.75)
for lvl in (3, 4):
    guard = 0
    while play.mode.level < lvl and guard < 60 * 400:
        advance(app, 1, draw_last=False)
        guard += 1
    advance(app, 4 * 60 + 90)
    shot(app, f"s4_endless_level{lvl}")
print("done")
