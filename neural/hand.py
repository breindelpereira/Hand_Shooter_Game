"""Webcam -> MediaPipe HandLandmarker -> aim point + fire gesture (fist or pinch).

The camera/MediaPipe loop runs in its own thread so the game loop stays at 60 FPS even
though the camera delivers ~30 FPS.  The game thread calls ``poll()`` each frame.

Two fire gestures are tracked at all times, so switching is instant:
  * "fist"  - finger-curl ratio drops when the hand closes
  * "pinch" - thumb-tip to index-tip distance (relative to palm size) drops on a pinch
Both go through the same swing detector (filters.FistDetector).
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
        # state written by the worker thread
        self._nx = self._ny = 0.5        # filtered (+ lead) aim, 0..1 over the active zone
        self._ratio = GESTURES[self.gesture]["open"]
        self._closed = False
        self._present = False
        self._lost_handled = False
        self._last_seen = 0.0
        self._fire_count = 0
        self._fire_pos = (0.5, 0.5)
        self._preview = None
        self.fps = 0.0
        self.brightness = 255.0
        # state owned by the game thread
        self._seen_fire = 0
        self._x, self._y = C.W / 2.0, C.H / 2.0
        self._had_hand = False
        # processing objects
        self._fx, self._fy = OneEuro(), OneEuro()
        self._lead = LeadPredictor()
        self._det = {g: make_detector(g) for g in GESTURES}
        # silent calibration: learn the player's open/closed levels while they play, unless a
        # manual calibration was loaded / run for that gesture
        self._manual = {g: False for g in GESTURES}
        self._adapt = {"fist": RefAdapter(self._det["fist"], (1.35, 2.5), (0.35, 1.25)),
                       "pinch": RefAdapter(self._det["pinch"], (0.45, 1.5), (0.04, 0.45))}
        self._sig = {g: GESTURES[g]["open"] for g in GESTURES}
        # session recording (F8)
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
        return self._det[self.gesture]

    def spec(self) -> dict:
        return GESTURES[self.gesture]

    def set_gesture(self, name: str):
        if name in GESTURES and name != self.gesture:
            with self._lock:
                self.gesture = name
                self._closed = self._det[name].closed
                self._ratio = self._sig[name]

    def set_calibration(self, open_ratio: float, closed_ratio: float, gesture: str | None = None) -> bool:
        g = gesture or self.gesture
        with self._lock:
            ok = self._det[g].calibrate(open_ratio, closed_ratio)
            if ok:
                self._manual[g] = True
            return ok

    def thresholds(self):
        d = self._active
        return d.close_thr, d.open_thr

    def meter(self):
        """(value, closed_ref, gate_hi, open_ref, closed) for the on-screen gesture meter."""
        d = self._active
        with self._lock:
            return self._ratio, d.closed_ref, d.gate_hi, d.open_ref, self._closed

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
            d = {g: (self._det[g].open_ref, self._det[g].closed_ref) for g in GESTURES}
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

            opts = vision.HandLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(C.MODEL_PATH)),
                running_mode=vision.RunningMode.VIDEO,
                num_hands=1,
                # lower than the 0.5 defaults: a quickly closing fist is the pose MediaPipe
                # is most likely to drop, and losing the hand mid-flick means a missed shot
                min_hand_detection_confidence=0.4,
                min_hand_presence_confidence=0.3,
                min_tracking_confidence=0.3,
            )
            landmarker = vision.HandLandmarker.create_from_options(opts)
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

                lm = res.hand_landmarks[0] if res.hand_landmarks else None
                self._process(lm, now, w, h)
                self._make_preview(cv2, frame, lm, w, h)

                frames += 1
                if now - fps_t >= 1.0:
                    self.fps = frames / (now - fps_t)
                    frames, fps_t = 0, now
        finally:
            landmarker.close()
            cap.release()

    def _record(self, now, present, x, y, fist, pinch, closed, fired):
        if self._rec is not None:
            self._rec.append((round(now - self._rec_t0, 4), int(present), round(x, 4), round(y, 4),
                              "" if fist is None else round(fist, 4),
                              "" if pinch is None else round(pinch, 4), int(closed), int(fired)))

    def _process(self, lm, now: float, w: int, h: int):
        with self._lock:
            act = self._active
            if lm is None:
                self._record(now, False, self._nx, self._ny, None, None, self._closed, False)
                if self._present:
                    lost_for = now - self._last_seen
                    if not self._lost_handled and lost_for > 0.06:
                        # the tracker often drops a hand the moment it becomes a fist
                        self._lost_handled = True
                        closed, fired, fpos = act.lost(now)
                        self._closed = closed
                        if fired:
                            self._fire_count += 1
                            self._fire_pos = fpos
                    # a closed hand that vanishes is most likely still closed: hold longer
                    grace = 0.9 if act.closed else 0.25
                    if lost_for > grace:
                        self._present = False
                        self._closed = False
                        self._fx.reset()
                        self._fy.reset()
                        self._lead.reset()
                        for d in self._det.values():
                            d.reset()
                return
            self._lost_handled = False
            aspect = w / h
            fist = curl_ratio(lm, aspect)
            pinch = pinch_ratio(lm, aspect)
            cx, cy = palm_center(lm)
            # map the active zone of the camera frame to 0..1
            zx = (cx - (C.ZONE_CX - C.ZONE_W / 2)) / C.ZONE_W
            zy = (cy - (C.ZONE_CY - C.ZONE_H / 2)) / C.ZONE_H
            zx = min(max(zx, 0.0), 1.0)
            zy = min(max(zy, 0.0), 1.0)
            fx, fy = self._fx(zx, now), self._fy(zy, now)
            px, py = self._lead(now, fx, fy)      # latency-compensated aim
            self._sig["fist"], self._sig["pinch"] = fist, pinch
            events = {}
            for g, det in self._det.items():
                events[g] = det.update(now, fist if g == "fist" else pinch, px, py)
            closed, fired, fpos = events[self.gesture]
            if not self._manual[self.gesture]:
                self._adapt[self.gesture].feed(self._sig[self.gesture], closed)
            self._nx, self._ny = px, py
            self._ratio = self._sig[self.gesture]
            self._closed = closed
            self._present = True
            self._last_seen = now
            if fired:
                self._fire_count += 1
                self._fire_pos = fpos
            self._record(now, True, px, py, fist, pinch, closed, fired)

    def _make_preview(self, cv2, frame, lm, w, h):
        # zone rectangle
        x0 = int((C.ZONE_CX - C.ZONE_W / 2) * w)
        x1 = int((C.ZONE_CX + C.ZONE_W / 2) * w)
        y0 = int((C.ZONE_CY - C.ZONE_H / 2) * h)
        y1 = int((C.ZONE_CY + C.ZONE_H / 2) * h)
        cv2.rectangle(frame, (x0, y0), (x1, y1), (255, 229, 0), 2)
        if lm is not None:
            pts = [(int(p.x * w), int(p.y * h)) for p in lm]
            col = (80, 255, 120) if self._closed else (255, 255, 255)
            for a, b in _CONNECTIONS:
                cv2.line(frame, pts[a], pts[b], col, 2)
            for p in pts:
                cv2.circle(frame, p, 3, (60, 60, 255), -1)
            if self.gesture == "pinch":                     # show what the pinch detector sees
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

    def poll(self, dt: float) -> InputState:
        with self._lock:
            present = self._present
            nx, ny = self._nx, self._ny
            closed = self._closed
            ratio = self._ratio
            fc = self._fire_count
            fpos = self._fire_pos
        if present:
            tx, ty = nx * C.W, ny * C.H
            if not self._had_hand:                 # snap on (re)acquire, don't slide in
                self._x, self._y = tx, ty
            else:
                k = 1.0 - math.exp(-dt * C.RETICLE_FOLLOW)
                self._x += (tx - self._x) * k
                self._y += (ty - self._y) * k
        self._had_hand = present
        fire = fc != self._seen_fire
        self._seen_fire = fc
        return InputState(x=self._x, y=self._y, present=present, fist=closed and present,
                          fire=fire and present, fire_x=fpos[0] * C.W, fire_y=fpos[1] * C.H,
                          ratio=ratio)
