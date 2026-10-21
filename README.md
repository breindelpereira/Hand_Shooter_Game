# Neural Targeting

A hand-tracked arcade shooter. You fly forward through a neon tunnel; open your hand to aim,
close your fist to fire. Built with Python, OpenCV, MediaPipe, NumPy and Pygame.

## Run

```bash
pip install -r requirements.txt
python main.py                   # hand tracking (falls back to mouse if no camera)
python main.py --input mouse     # force mouse: move to aim, click or Space to fire
python main.py --windowed        # start in a window instead (full screen is the default; remembered)
python main.py --easy            # start with auto-fire on (also a toggle in the menu)
python main.py --gesture clock   # CLOCK IT mode: fire with a thumb+index pinch, pink-purple world (also a menu choice)
python main.py --input replay --replay data/sessions/session_X.csv   # play back a recorded session
python main.py --duration 70     # 70-second Score Attack
python main.py --timed-lives 3   # give Score Attack 3 lives
```

The hand model `models/hand_landmarker.task` must be present (it is the official MediaPipe
`hand_landmarker/float16/1` model).

**Tips for tracking:** good front lighting (the camera drops to ~10 FPS in a dark room), keep your hand
inside the cyan box in the camera preview, and run the calibration once (it starts automatically the
first time; re-run it from the menu).

## Controls

| Hand | Action |
|---|---|
| Open hand, move | Aim the reticle |
| Close fist **or** pinch thumb + index (pick in the menu: `FIRE: FIST / PINCH`) | Fire (the shot lands where your palm was just *before* the gesture) |
| Hold the gesture ~0.5 s, then open | Charged blast (uses a charge cell, earned every 8 kills; clears mines safely) |
| Hover a menu button + close fist | Select (or hold still for ~1 s) |

Keyboard (convenience only): `Esc`/`P` pause, `M` mute, `B` bloom, `C` camera preview, `F3` debug,
`F8` record a hand session, `F11` full screen on/off (also the **FULLSCREEN** button, top-right of the menu).
The game runs full screen by default and remembers your choice; it renders at 1280x720 and is scaled to fit
(letterboxed on other aspect ratios).

## Easy mode (auto-fire)

Toggle **EASY MODE - AUTO-FIRE** on the main menu (the choice is remembered). The game pulls the trigger,
but **aiming is the skill**: put the reticle *on* a target and hold it there; a green ring fills and it fires.

- the lock zone is sized from the target itself (small targets need a steadier hand),
- you must **hold** on it ~0.35 s, so sweeping your hand across the screen does nothing,
- a held lock forgives a little drift, and the reticle gets a light pull toward the target,
- the shot goes to the target's centre (no "perfect" bonus in easy mode),
- mines are never locked, and auto-fire never shoots one (the Overcharge side rays are dropped if they
  would catch a mine).

Measured with a bot that aims perfectly but with a shaky hand, over 45 s: tremor of +-5..15 px clears
~45 targets, +-30 px ~27, +-45 px ~7, and waving around blindly 0. Easy runs have their **own high-score
tables** (switch between NORMAL and EASY on the High Scores screen). Holding the gesture still charges the
blast if you want it.

Tune it in `neural/config.py`: `AUTO_LOCK_TIME` (hold time), `AUTO_CAPTURE_PX` / `AUTO_CAPTURE_SCALE` /
`AUTO_CAPTURE_PAD` (zone size), `AUTO_STICK`, `AUTO_MAGNET`, `AUTO_FIRE_INTERVAL`. Normal mode's hit radius
(`HIT_ASSIST`, `HIT_MIN_RADIUS`) is separate and more forgiving.

## High-score entry

A wide name field (up to **20 characters**, with a counter and blinking caret). Pick letters with the reticle
(close your fist / pinch, or hold still), **SPACE** adds a space, **DEL** removes a character, **SAVE** stores the
name, and **NO NAME** saves the score anonymously as `---`. With a keyboard you can also just type (letters,
digits, space, Backspace, Enter). Long names are shrunk to fit on the High Scores screen. The limit is
`NAME_MAX` in `neural/config.py`.

## Fist detection

The detector watches the *swing* of your finger curl rather than waiting for a fully open/closed
hand, so quick flicks and partial re-opens count, and it adapts to jitter so a held fist doesn't fire by
itself. MediaPipe often loses the hand the instant it becomes a tight fist; that is handled too (a hand
that vanishes mid-curl counts as a shot, and a vanished fist keeps its state). Next to the camera preview
is a **fist meter**: the dot is your current curl, the orange line is the fire threshold, and the preview
border flashes gold on every shot. Tune `FIST_SWING` / `FIST_GATE` in `neural/config.py` if needed.

## Pinch-to-fire (CLOCK IT mode)

Closing a fist drags the palm, is slow to repeat, and is the pose MediaPipe most often drops. A thumb-index
**pinch** keeps the hand open and tracked and barely moves the aim point. Choose it with the `FIRE:` button
on the choose-your-game screen (the button is called **CLOCK IT**; the whole game turns pink-purple in this mode). Both gestures are tracked all the time through
the same swing detector, so switching is instant. The camera preview draws a line between thumb and index
tip while pinch is selected.

## Latency compensation

The camera plus MediaPipe add ~100 ms. The aim point is extrapolated a few tens of ms along the hand's
smoothed velocity (`AIM_LEAD`, fades to zero when the hand is still so it never amplifies jitter), and the
fire position is taken from the same predicted track, so shots land where the reticle was. Set
`AIM_LEAD = 0` in `neural/config.py` to turn it off.

## Recording and tuning from real data

Press `F8` in the game to start/stop recording; a CSV lands in `data/sessions/`. Then:

```bash
python tools/analyze_session.py data/sessions/session_YYYYMMDD_HHMMSS.csv
python tools/analyze_session.py <file> --swing 0.22 --gate 0.7    # re-run the detector with other settings
```

It reports camera FPS, tracking dropouts, the range and jitter of each gesture signal, and how many shots the
detector would have fired. `--input replay --replay <file>` plays a session back as game input.
**A slow camera makes fast flicks hard to catch**: in a dim room many webcams drop to ~10 FPS, so a flick is
only 1-2 frames. The preview shows `CAMERA n FPS - ADD LIGHT` when the camera is slow (or covered). In a lit
room most webcams run at 30 FPS.

## Targets fly in on paths

Targets no longer all emerge from one point: many **swoop** in from the sides and top and settle into their
lane, and Score Attack / Survival spawn **formations** - V-waves, arcs and rings that spin as they approach
(great for charged blasts). The scenery also leans slightly opposite to your hand for a parallax depth cue.

## Walk-up flow (built for an event)

1. **Home / attract screen:** a demo plays behind a single big **PLAY** circle. Just *hold your open hand in
   the circle* for a second (or fist / pinch on it) - no buttons needed. The screen shows **today's top
   player** and the best ever. Small SCORES / SETTINGS / QUIT buttons sit at the bottom.
2. **Choose your game:** two big cards (Score Attack / Survival, each showing your best), then simple either/or
   choices: **EASY / NORMAL** and **FIST / PINCH**. The selected option is highlighted; choices are remembered.
3. **10-second trial** (first game of a visit only): aim at one big cube and shoot it. Skippable, and never
   longer than `TRIAL_TIMEOUT`.
4. **Play** -> results -> name (or NO NAME).

Event helpers:
- **Auto-reset after idle:** with a camera, if nobody has been in view for `IDLE_RESET` (15 s) the game drops
  whatever is on screen (a half-played game, someone's name entry) and returns to the home screen, and the next
  visitor gets the trial again.
- **"Step in" guide:** on the home and choose screens, while no hand is visible, the camera view grows, its
  border pulses orange and it says *STEP IN - RAISE YOUR HAND IN THE BOX*.
- **No per-person calibration:** the detector starts from sensible defaults and *silently learns the player's
  open/closed levels while they play* (small, clamped adjustments, only when both states are clearly seen). A
  manual calibration (Settings) still exists and overrides this.
- **Menu feel:** buttons lean toward the reticle as it approaches, glow, and pop with a "bloop" when chosen.
- **Settings:** fullscreen, camera view shown/hidden (privacy), sound, optional calibration, full how-to-play.

## Full tutorial

An interactive tutorial (aim into two rings, shoot three cubes, then shoot a cube but let a mine pass) is under
**Settings -> HOW TO PLAY**.

## Look

A neon city canyon: rows of tower blocks with lit windows, floating cubes, gates, a ringed planet, a skyline
and a perspective grid floor. Each Survival level gets its own colour world (neon, toxic, inferno, void,
ice); Score Attack shifts from neon to void to inferno as it heats up. Enemies are solid, shaded, spinning
cubes; power-ups are round orbs; mines are spiked.

## Modes

- **Score Attack** - 5 s get-ready countdown (starts only once your hand is seen), slow start, then an
  **adaptive ramp** over the 60 s clock: kills and combos raise the difficulty, leaks and mine hits lower it,
  so a struggling player stays at a gentle pace while a strong one is pushed harder (faster, more formations,
  more mine / small / shielded targets). Higher intensity pays up to **x1.6 points** (see the INTENSITY meter
  under the timer). No lives: missed targets cost -50 and your combo,
  shooting a mine costs -200.
- **Survival** - 3 lives and endless levels. Level 1 has a few slow targets; every level adds more and
  introduces something new (fast, small, mini-boss, mines, splitters, shielded, boss every 10th).
  A short break between levels; a flawless level earns a life.

## Targets

| Colour | Kind | Notes |
|---|---|---|
| Cyan cube | Standard | 100 pts |
| Orange stretched cube | Fast | moves 1.55x faster |
| Magenta cube | Small | half size, orbiting dots |
| Green cube with inner cube | Splitter | breaks into two mini targets |
| White cube + shield arcs | Shielded | needs two hits |
| Red star | **Mine** | do **not** shoot (costs a life / points). Letting it pass is safe |
| Gold clock / purple bolt / blue plus orbs | Power-ups | Time Warp, Overcharge (triple shot), Repair |
| Big red cube + gold node cubes | Boss | destroy all orbiting nodes before it reaches you |

## Project layout

```
main.py                  entry point / CLI
neural/
  config.py              every tunable constant
  filters.py             One Euro smoothing + fist detector (pure, unit tested)
  hand.py                webcam + MediaPipe worker thread (fist + pinch signals, lead, recorder)
  inputs.py              InputState, MouseInput, BotInput
  replay.py              session CSV loader + ReplayInput
  world.py               pseudo-3D targets, hitscan, blast (pure logic)
  modes.py               TimedMode, EndlessMode, level_spec()
  scoring.py             score/combo/rank + persistent high scores
  fx.py  render.py       glow, particles, 3D cubes, target/reticle/HUD drawing
  scenery.py             city canyon: sky, planet, skyline, floor grid, blocks, gates, themes
  audio.py               fully synthesised SFX + layered music
  ui.py  scenes.py       hand-operated buttons; menu/play/results/scores/calibrate
  app.py                 window + main loop
tools/
  analyze_session.py     report on a recorded hand session
tests/
  run_tests.py           dependency-free test runner (pytest also works)
  smoke.py               headless end-to-end run with screenshots (incl. auto-fire check)
  shots.py               gameplay screenshots across colour themes
  gallery.py             renders every target type
data/                    scores.json, calibration.json, settings.json, sessions/ (created at runtime)
```

## Tests

```bash
python tests/run_tests.py
python tests/smoke.py out_dir      # headless full run + screenshots
```
