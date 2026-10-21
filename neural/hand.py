"""Webcam -> MediaPipe HandLandmarker -> aim point + fire gesture (fist or pinch/"clock it").

The camera/MediaPipe loop runs in its own thread so the game loop stays at 60 FPS even
though the camera delivers ~30 FPS.  The game thread calls ``poll_players()`` each frame.

Two fire gestures are tracked at all times, so switching is instant:
  * "fist"  - finger-curl ratio drops when the hand closes
  * "pinch" - thumb-tip to index-tip distance (relative to palm size) drops on a pinch
Both go through the same swing detector (filters.FistDetector).

Each tracked hand lives in a ``HandChannel`` (its own smoothing, lead, detectors, silent
calibration).  Single player uses channel 0 over the centre of the frame; two players use
channel 0 for the LEFT half of the (mirrored) camera view and channel 1 for the RIGHT half.
"""
from __future__ import annotations

import csv
import math
import threading
import time
from datetime import datetime

from . import config as C
from .filters import FistDetector, LeadPredictor, OneEuro, RefAdapter
from .inputs import InputProvider, InputState

# MediaPipe hand skeleton (for the preview overlay)
_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10),
    (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18),
    (18, 19), (19, 20), (0, 17),
]
_TIPS = (8, 12, 16, 20)
_MCPS = (5, 9, 13, 17)
_PALM = (0, 5, 9, 13, 17)

# per-gesture defaults and the sanity gates the calibration screen uses
GESTURES = {
    "fist": dict(label="FIST", verb="CLOSE FIST", open=1.80, closed=0.90, open_min=1.30,
                 min_sep=0.35, tip="Close your hand into a tight fist and hold it"),
    "pinch": dict(label="CLOCK IT", verb="PINCH", open=0.85, closed=0.20, open_min=0.45,
                  min_sep=0.25, tip="Pinch your thumb and index finger together and hold"),
}

# camera-frame zones (cx, cy, w, h as fractions of the frame) that map onto the whole screen
ZONE_SOLO = (C.ZONE_CX, C.ZONE_CY, C.ZONE_W, C.ZONE_H)
ZONE_P1 = (0.27, 0.50, 0.40, 0.60)        # left half of the mirrored view
ZONE_P2 = (0.73, 0.50, 0.40, 0.60)        # right half


def curl_ratio(lm, aspect: float) -> float:
    """Mean fingertip-to-wrist / knuckle-to-wrist distance for the four fingers."""
    wx, wy = lm[0].x * aspect, lm[0].y
    total = 0.0
    for tip, mcp in zip(_TIPS, _MCPS):
        dt = math.hypot(lm[tip].x * aspect - wx, lm[tip].y - wy)
        dm = math.hypot(lm[mcp].x * aspect - wx, lm[mcp].y - wy)
        total += dt / max(dm, 1e-6)
    return total / 4.0


def pinch_ratio(lm, aspect: float) -> float:
    """Thumb-tip to index-tip distance in units of palm size (wrist to middle knuckle)."""
    d = math.hypot((lm[4].x - lm[8].x) * aspect, lm[4].y - lm[8].y)
    palm = math.hypot((lm[0].x - lm[9].x) * aspect, lm[0].y - lm[9].y)
    return d / max(palm, 1e-6)


def palm_center(lm):
    n = len(_PALM)
    return sum(lm[i].x for i in _PALM) / n, sum(lm[i].y for i in _PALM) / n


def make_detector(gesture: str) -> FistDetector:
    g = GESTURES[gesture]
    return FistDetector(open_ref=g["open"], closed_ref=g["closed"], min_span=g["min_sep"])


def assign_hands(palm_xs, prev):
    """Decide which detected hand belongs to which player (index 0 = left of the view).

    palm_xs: palm-centre x (0..1, mirrored view) of each detected hand.
    prev:    last known palm x of each player's channel, or None if not recently seen.
    Returns [index_into_palm_xs or None] for player 0 and player 1.
    Two hands: left-most -> P1, right-most -> P2.  One hand: stick with the player whose last
    position is nearest (so a hand drifting past the middle does not swap players); if nobody
    was tracked yet, the side of the midline decides."""
    n = len(palm_xs)
    if n == 0:
        return [None, None]
    if n >= 2:
        order = sorted(range(n), key=lambda i: palm_xs[i])
        return [order[0], order[-1]]
    x = palm_xs[0]
    p0, p1 = prev
    if p0 is None and p1 is None:
        return [0, None] if x < 0.5 else [None, 0]
    if p0 is not None and p1 is not None:
        return [0, None] if abs(x - p0) <= abs(x - p1) else [None, 0]
    if p0 is not None:                                   # only P1 was tracked
        return [0, None] if (abs(x - p0) < 0.28 or x < 0.5) else [None, 0]
    return [None, 0] if (abs(x - p1) < 0.28 or x >= 0.5) else [0, None]


class HandChannel:
    """Everything that belongs to ONE tracked hand: filtering, gesture detection, silent
    calibration, and the reticle smoothing the game thread reads."""

    def __init__(self, zone):
        self.zone = zone
        self.fx, self.fy = OneEuro(), OneEuro()
        self.lead = LeadPredictor()
        self.det = {g: make_detector(g) for g in GESTURES}
        self.manual = {g: False for g in GESTURES}
        self.adapt = {"fist": RefAdapter(self.det["fist"], (1.35, 2.5), (0.35, 1.25)),
                      "pinch": RefAdapter(self.det["pinch"], (0.45, 1.5), (0.04, 0.45))}
        self.sig = {g: GESTURES[g]["open"] for g in GESTURES}
        # published by the worker thread
        self.nx = self.ny = 0.5
        self.ratio = GESTURES["fist"]["open"]
        self.closed = False
        self.present = False
        self.lost_handled = False
        self.last_seen = 0.0
        self.palm_x = 0.5
        self.fire_count = 0
        self.fire_pos = (0.5, 0.5)
        # owned by the game thread
        self.seen_fire = 0
        self.gx, self.gy = C.W / 2.0, C.H / 2.0
        self.had_hand = False

    def reset_tracking(self):
        self.present = False
        self.closed = False
        self.fx.reset()
        self.fy.reset()
        self.lead.reset()
        for d in self.det.values():
            d.reset()

    def recent_palm_x(self, now):
        return self.palm_x if (self.present and now - self.last_seen < 0.6) else None

    def process(self, lm, now: float, aspect: float, gesture: str):
        """Feed one frame (lm=None when this player's hand was not seen).
        Returns a row for the session recorder: (present, x, y, fist, pinch, closed, fired)."""
        act = self.det[gesture]
        if lm is None:
            row = (False, self.nx, self.ny, None, None, self.closed, False)
            if self.present:
                lost_for = now - self.last_seen
                if not self.lost_handled and lost_for > 0.06:
                    # the tracker often drops a hand the moment it becomes a fist
                    self.lost_handled = True
                    closed, fired, fpos = act.lost(now)
                    self.closed = closed
                    if fired:
                        self.fire_count += 1
                        self.fire_pos = fpos
                        row = (False, self.nx, self.ny, None, None, closed, True)
                # a closed hand that vanishes is most likely still closed: hold longer
                grace = 0.9 if act.closed else 0.25
                if lost_for > grace:
                    self.reset_tracking()
            return row
        self.lost_handled = False
        fist = curl_ratio(lm, aspect)
        pinch = pinch_ratio(lm, aspect)
        cx, cy = palm_center(lm)
        self.palm_x = cx
        zcx, zcy, zw, zh = self.zone
        zx = min(max((cx - (zcx - zw / 2)) / zw, 0.0), 1.0)
        zy = min(max((cy - (zcy - zh / 2)) / zh, 0.0), 1.0)
        fx, fy = self.fx(zx, now), self.fy(zy, now)
        px, py = self.lead(now, fx, fy)                 # latency-compensated aim
        self.sig["fist"], self.sig["pinch"] = fist, pinch
        events = {g: d.update(now, fist if g == "fist" else pinch, px, py) for g, d in self.det.items()}
        closed, fired, fpos = events[gesture]
        if not self.manual[gesture]:
            self.adapt[gesture].feed(self.sig[gesture], closed)
        self.nx, self.ny = px, py
        self.ratio = self.sig[gesture]
        self.closed = closed
        self.present = True
        self.last_seen = now
        if fired:
            self.fire_count += 1
            self.fire_pos = fpos
        return (True, px, py, fist, pinch, closed, fired)

    def to_state(self, dt: float) -> InputState:
        """Game-thread side: ease the reticle toward the filtered hand position."""
        if self.present:
            tx, ty = self.nx * C.W, self.ny * C.H
            if not self.had_hand:                       # snap on (re)acquire, don't slide in
                self.gx, self.gy = tx, ty
            else:
                k = 1.0 - math.exp(-dt * C.RETICLE_FOLLOW)
                self.gx += (tx - self.gx) * k
                self.gy += (ty - self.gy) * k
        self.had_hand = self.present
        fire = self.fire_count != self.seen_fire
        self.seen_fire = self.fire_count
        return InputState(x=self.gx, y=self.gy, present=self.present,
                          fist=self.closed and self.present, fire=fire and self.present,
                          fire_x=self.fire_pos[0] * C.W, fire_y=self.fire_pos[1] * C.H,
                          ratio=self.ratio)


class HandInput(InputProvider):
    name = "hand"
    has_camera = True

    def __init__(self, camera_index: int = C.CAMERA_INDEX, gesture: str = C.DEFAULT_GESTURE):
        self.camera_index = camera_index
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error: str | None = None
        self._thread: threading.Thread | None = None
        self.gesture = gesture if gesture in GESTURES else "fist"
        self.players = 1
        self._want_hands = 1
        self.chs = [HandChannel(ZONE_SOLO), HandChannel(ZONE_P2)]
        self._assign = [None, None]
        self._preview = None
        self.fps = 0.0
        self.brightness = 255.0
        # session recording (F8) - records player 1's channel
        self._rec: list | None = None
        self._rec_t0 = 0.0

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        if not C.MODEL_PATH.exists():
            raise RuntimeError(f"hand model not found: {C.MODEL_PATH}")
        self._thread = threading.Thread(target=self._run, name="hand-tracker", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=15):
            raise RuntimeError("camera / hand model did not start in time")
        if self._error:
            raise RuntimeError(self._error)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    # ---- configuration -------------------------------------------------------
    @property
    def _active(self) -> FistDetector:
        return self.chs[0].det[self.gesture]

    def spec(self) -> dict:
        return GESTURES[self.gesture]

    def set_gesture(self, name: str):
        if name in GESTURES and name != self.gesture:
            with self._lock:
                self.gesture = name
                for ch in self.chs:
                    ch.closed = ch.det[name].closed
                    ch.ratio = ch.sig[name]

    def set_players(self, n: int):
        """1 = one hand over the middle of the view; 2 = left half / right half."""
        n = 2 if n >= 2 else 1
        with self._lock:
            if n == self.players:
                return
            self.players = n
            self._want_hands = n
            self.chs[0].zone = ZONE_P1 if n == 2 else ZONE_SOLO
            self.chs[1].zone = ZONE_P2
            for ch in self.chs:
                ch.reset_tracking()
                ch.had_hand = False

    def set_calibration(self, open_ratio: float, closed_ratio: float, gesture: str | None = None) -> bool:
        g = gesture or self.gesture
        with self._lock:
            ok = False
            for ch in self.chs:
                ok = ch.det[g].calibrate(open_ratio, closed_ratio) or ok
                if ok:
                    ch.manual[g] = True
            return ok

    def thresholds(self):
        d = self._active
        return d.close_thr, d.open_thr

    def meter(self):
        """(value, closed_ref, gate_hi, open_ref, closed) for the on-screen gesture meter."""
        d = self._active
        ch = self.chs[0]
        with self._lock:
            return ch.ratio, d.closed_ref, d.gate_hi, d.open_ref, ch.closed

    # ---- session recording ---------------------------------------------------
    @property
    def recording(self) -> bool:
        return self._rec is not None

    def start_recording(self):
        with self._lock:
            self._rec = []
            self._rec_t0 = time.perf_counter()

    def stop_recording(self):
        """Write the CSV and return its path (or None if nothing was recorded)."""
        with self._lock:
            rows, self._rec = self._rec, None
            d = {g: (self.chs[0].det[g].open_ref, self.chs[0].det[g].closed_ref) for g in GESTURES}
            active = self.gesture
        if not rows:
            return None
        out_dir = C.DATA_DIR / "sessions"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"session_{datetime.now():%Y%m%d_%H%M%S}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write(f"# gesture={active} fist_open={d['fist'][0]:.3f} fist_closed={d['fist'][1]:.3f} "
                    f"pinch_open={d['pinch'][0]:.3f} pinch_closed={d['pinch'][1]:.3f} "
                    f"swing={C.FIST_SWING} gate={C.FIST_GATE} lead={C.AIM_LEAD}\n")
            w = csv.writer(f)
            w.writerow(["t", "present", "x", "y", "fist", "pinch", "closed", "fired"])
            w.writerows(rows)
        return path

    # ---- worker thread ---------------------------------------------------
    def _run(self):
        import cv2
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        def make_landmarker(n_hands: int):
            opts = vision.HandLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(C.MODEL_PATH)),
                running_mode=vision.RunningMode.VIDEO,
                num_hands=n_hands,
                # lower than the 0.5 defaults: a quickly closing fist is the pose MediaPipe
                # is most likely to drop, and losing the hand mid-flick means a missed shot
                min_hand_detection_confidence=0.4,
                min_hand_presence_confidence=0.3,
                min_tracking_confidence=0.3,
            )
            return vision.HandLandmarker.create_from_options(opts)

        cap = None
        try:
            backend = getattr(cv2, "CAP_DSHOW", 0) if __import__("sys").platform == "win32" else 0
            cap = cv2.VideoCapture(self.camera_index, backend)
            if not cap.isOpened():
                raise RuntimeError(f"cannot open camera {self.camera_index}")
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, C.CAM_W)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, C.CAM_H)
            cap.set(cv2.CAP_PROP_FPS, 30)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            cur_hands = self._want_hands
            landmarker = make_landmarker(cur_hands)
        except Exception as exc:  # noqa: BLE001 - report to the main thread
            self._error = str(exc)
            self._ready.set()
            if cap is not None:
                cap.release()
            return

        self._ready.set()
        t0 = time.perf_counter()
        last_ts = -1
        frames, fps_t = 0, t0
        try:
            while not self._stop.is_set():
                if self._want_hands != cur_hands:        # 1 <-> 2 players: rebuild the tracker
                    landmarker.close()
                    cur_hands = self._want_hands
                    landmarker = make_landmarker(cur_hands)
                    last_ts = -1
                    t0 = time.perf_counter()
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.01)
                    continue
                frame = cv2.flip(frame, 1)               # mirror: right hand -> right side
                h, w = frame.shape[:2]
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                now = time.perf_counter()
                ts = int((now - t0) * 1000)
                if ts <= last_ts:
                    ts = last_ts + 1
                last_ts = ts
                res = landmarker.detect_for_video(
                    mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)

                lms = list(res.hand_landmarks) if res.hand_landmarks else []
                self._process(lms, now, w, h)
                self._make_preview(cv2, frame, lms, w, h)

                frames += 1
                if now - fps_t >= 1.0:
                    self.fps = frames / (now - fps_t)
                    frames, fps_t = 0, now
        finally:
            landmarker.close()
            cap.release()

    def _record(self, now, row):
        if self._rec is not None:
            present, x, y, fist, pinch, closed, fired = row
            self._rec.append((round(now - self._rec_t0, 4), int(present), round(x, 4), round(y, 4),
                              "" if fist is None else round(fist, 4),
                              "" if pinch is None else round(pinch, 4), int(closed), int(fired)))

    def _process(self, lms, now: float, w: int, h: int):
        with self._lock:
            aspect = w / h
            if self.players == 1:
                assign = [0 if lms else None, None]
            else:
                prev = [ch.recent_palm_x(now) for ch in self.chs]
                assign = assign_hands([palm_center(lm)[0] for lm in lms], prev)
            self._assign = assign
            rows = []
            for i, ch in enumerate(self.chs):
                idx = assign[i]
                rows.append(ch.process(lms[idx] if idx is not None else None, now, aspect, self.gesture))
            self._record(now, rows[0])

    def _make_preview(self, cv2, frame, lms, w, h):
        with self._lock:
            assign = list(self._assign)
            zones = [self.chs[0].zone] + ([self.chs[1].zone] if self.players == 2 else [])
            closed = [ch.closed for ch in self.chs]
            gesture = self.gesture
        colors = [(255, 229, 0), (255, 80, 220)]            # BGR-ish tints: cyan-ish / magenta-ish
        for i, (zcx, zcy, zw, zh) in enumerate(zones):
            x0, x1 = int((zcx - zw / 2) * w), int((zcx + zw / 2) * w)
            y0, y1 = int((zcy - zh / 2) * h), int((zcy + zh / 2) * h)
            cv2.rectangle(frame, (x0, y0), (x1, y1), colors[i], 2)
        for pi, idx in enumerate(assign):
            if idx is None or idx >= len(lms):
                continue
            lm = lms[idx]
            pts = [(int(p.x * w), int(p.y * h)) for p in lm]
            col = (80, 255, 120) if closed[pi] else (255, 255, 255)
            for a, b in _CONNECTIONS:
                cv2.line(frame, pts[a], pts[b], col, 2)
            for p in pts:
                cv2.circle(frame, p, 3, (60, 60, 255), -1)
            if gesture == "pinch":                           # show what the pinch detector sees
                cv2.line(frame, pts[4], pts[8], (0, 200, 255), 3)
        small = cv2.resize(frame, (320, 240), interpolation=cv2.INTER_AREA)
        small = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        with self._lock:
            self._preview = small
            self.brightness = float(small.mean())

    # ---- game thread -----------------------------------------------------
    def preview(self):
        with self._lock:
            return None if self._preview is None else self._preview.copy()

    def poll_players(self, dt: float):
        with self._lock:
            return [self.chs[0].to_state(dt), self.chs[1].to_state(dt)]

    def poll(self, dt: float) -> InputState:
        return self.poll_players(dt)[0]
