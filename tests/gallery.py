"""Renders every target type up close into a PNG for visual inspection.
    python tests/gallery.py <output.png>
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
from neural.inputs import InputState  # noqa: E402
from neural.scenes import PlayScene  # noqa: E402

out = sys.argv[1] if len(sys.argv) > 1 else "gallery.png"
Z = float(sys.argv[2]) if len(sys.argv) > 2 else 9.0
CLOCK = float(sys.argv[3]) if len(sys.argv) > 3 else 2.9
app = App(input_mode="mouse", audio=False, skip_calibration=True)
sc = PlayScene(app, "endless")
app.goto(sc)
app._swap()
m = sc.mode
m.phase = "play"
m.update = lambda *a, **k: None          # freeze spawning
w = sc.world
kinds = ["standard", "fast", "small", "splitter", "mini", "shielded", "mine",
         "timewarp", "overcharge", "repair"]
for i, k in enumerate(kinds):
    row, col = divmod(i, 5)
    t = w.spawn(k, (col - 2) * 1.0, (row - 0.5) * 0.9 - 0.15, Z)
    t.age = 1.0
    t.speed = 0.0
    if t.wob_amp:
        t.wob_amp = 0.0
b = w.spawn_boss(nodes=3)
b.z = Z + 5
b.speed = 0.0
b.wob_amp = 0.0
b.x0 = b.x = 0.0
b.y = 1.2
b.age = 1.0
for n in b.children:
    n.age = 1.0
w.base_speed = 1.0
sc.scoring.score = 12340
sc.scoring.combo = 12
sc.scoring.charge = 2
sc.overcharge = 3.0
sc.fx.big_callout("RAMPAGE", C.ORANGE, 5.0)
sc.fx.popup(300, 300, "+1,250", C.GOLD, 30, life=5.0, rise=0)
sc.fx.explosion(1050, 600, C.CYAN, 1.5, big=True)
inp = InputState(x=640 + 1.0 * C.FOCAL / Z, y=C.CY + (-0.15 - 0.45) * C.FOCAL / Z, present=True)
app.clock_t = CLOCK
for _ in range(4):
    sc.update(1 / 60, inp)
    w.update(0.0)
sc.draw(app.canvas)
app._bloom(app.canvas)
pygame.image.save(app.canvas, out)
print("saved", out)
