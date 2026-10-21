"""Tunable constants for Neural Targeting. Everything gameplay-related lives here."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MODEL_PATH = ROOT / "models" / "hand_landmarker.task"
SCORES_PATH = DATA_DIR / "scores.json"
STATS_PATH = DATA_DIR / "stats.json"
CALIB_PATH = DATA_DIR / "calibration.json"

# ---- display -------------------------------------------------------------
W, H = 1280, 720
FPS = 60
CX, CY = W // 2, H // 2          # vanishing point

# ---- pseudo-3D -----------------------------------------------------------
FOCAL = 600.0
Z_FAR = 48.0                      # spawn depth
Z_PASS = 2.0                      # a target this close has "passed" the player
BOSS_PASS_Z = 3.5
BASE_SPEED = 11.0                 # world units / second at speed multiplier 1.0
X_RANGE = 1.7                     # lateral spawn range (world units)
Y_RANGE = 0.95
TUNNEL_W, TUNNEL_H = 3.6, 2.1

# ---- hand tracking -------------------------------------------------------
CAMERA_INDEX = 0
CAM_W, CAM_H = 640, 480
ZONE_W, ZONE_H = 0.60, 0.60       # fraction of the camera frame mapped to the screen
ZONE_CX, ZONE_CY = 0.5, 0.5
EURO_MIN_CUTOFF = 1.6
EURO_BETA = 7.0
EURO_D_CUTOFF = 1.0
RETICLE_FOLLOW = 30.0             # game-thread easing toward the filtered hand position
FIST_SWING = 0.30                 # curl swing (fraction of open-closed span) that counts as a flick
FIST_GATE = 0.66                  # a close must end below closed + this * span
FIST_MIN_GAP = 0.05               # seconds between two shots from the hand (guards double-fires)
AIM_LOCK_DELAY = 0.10             # seconds: shot lands where the palm was before closing
AIM_LEAD = 0.055                  # seconds of latency compensation (0 disables)
AIM_LEAD_MAX = 0.09               # cap on the lead displacement (fraction of the zone)
DEFAULT_GESTURE = "fist"          # "fist" or "pinch"
HAND_LOST_GRACE = 0.45            # seconds before the game pauses on tracking loss

# ---- combat --------------------------------------------------------------
HIT_ASSIST = 1.30                 # multiplier on on-screen radius (hands are not pixel-precise)
HIT_MIN_RADIUS = 26               # px
PERFECT_FRACTION = 0.30
SHOT_COOLDOWN = 0.05
CHARGE_HOLD = 0.55                # seconds of held fist before release fires the blast
BLAST_RADIUS = 260                # px
CHARGE_CELL_EVERY = 8             # kills per charge cell
CHARGE_CELLS_MAX = 3
MAX_MULT = 8
COMBO_PER_STEP = 5

# ---- easy mode (auto-fire: the player only aims) ---------------------------
# Easy mode makes *aiming* the skill: you must put the reticle on the target itself and hold it
# there. Loosen these if it is too hard (widen the zone / shorten the hold), tighten for more skill.
AUTO_LOCK_TIME = 0.35             # seconds you must HOLD on a target before it fires (stops sweeping)
AUTO_FIRE_INTERVAL = 0.30         # seconds between automatic shots
AUTO_CAPTURE_PX = 24              # lock zone radius for tiny targets (px)
AUTO_CAPTURE_SCALE = 1.0          # lock zone = target's on-screen radius x this ...
AUTO_CAPTURE_PAD = 6.0            # ... + this many px (never smaller than AUTO_CAPTURE_PX)
AUTO_STICK = 1.3                  # a held lock survives drifting this far (x lock zone)
AUTO_MAGNET = 0.25                # small visual pull of the reticle onto the locked target

# ---- timed mode ----------------------------------------------------------
TIMED_DURATION = 60.0             # set to 70.0 for the longer run
TIMED_COUNTDOWN = 5.0
TIMED_LIVES = None                # None = no lives (points penalties instead)
TIMED_LEAK_PENALTY = 50
TIMED_MINE_PENALTY = 200

# ---- endless mode --------------------------------------------------------
START_LIVES = 3
MAX_LIVES = 5
LEVEL_CLEAR_TIME = 2.2
LEVEL_INTRO_TIME = 1.6

# ---- power-ups -----------------------------------------------------------
TIMEWARP_TIME = 4.0
TIMEWARP_SCALE = 0.40
OVERCHARGE_TIME = 6.0
OVERCHARGE_SPREAD = 78            # px

NAME_MAX = 20                     # longest high-score name

# ---- two-player (set False to hide it everywhere; or `git checkout baseline-1p`) ----
TWO_PLAYER = True

# ---- walk-up / event flow ----------------------------------------------------
IDLE_RESET = 15.0                 # seconds with no hand in view -> back to the attract screen
PLAY_DWELL = 1.0                  # hold your hand in the big PLAY circle this long to start
TRIAL_TIMEOUT = 14.0              # the quick "try it" never lasts longer than this

# ---- UI ------------------------------------------------------------------
DWELL_TIME = 1.1
UI_LOCK_TIME = 0.8                # seconds after a scene opens before UI reacts

# ---- palette -------------------------------------------------------------
BG = (4, 5, 16)
CYAN = (0, 229, 255)
MAGENTA = (255, 40, 210)
ORANGE = (255, 150, 30)
GREEN = (70, 255, 130)
RED = (255, 50, 70)
GOLD = (255, 214, 60)
PURPLE = (175, 100, 255)
BLUE = (80, 150, 255)
WHITE = (235, 245, 255)
GREY = (168, 182, 208)            # brighter than before: easier to read on dark panels
