"""Signal processing for hand input: One Euro smoothing + fist detection.

Pure Python (no camera / MediaPipe / pygame imports) so it is unit-testable.
"""
from __future__ import annotations

import math
from collections import deque

from . import config as C


class OneEuro:
    """One Euro filter: low jitter when slow, low lag when fast."""

    def __init__(self, min_cutoff=C.EURO_MIN_CUTOFF, beta=C.EURO_BETA, d_cutoff=C.EURO_D_CUTOFF):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.reset()

    def reset(self):
        self._t = None
        self._x = 0.0
        self._dx = 0.0

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: float, t: float) -> float:
        if self._t is None:
            self._t, self._x, self._dx = t, x, 0.0
            return x
        dt = max(t - self._t, 1e-3)
        dx = (x - self._x) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        self._dx += a_d * (dx - self._dx)
        cutoff = self.min_cutoff + self.beta * abs(self._dx)
        a = self._alpha(cutoff, dt)
        self._x += a * (x - self._x)
        self._t = t
        return self._x


class FistDetector:
    """Turns a per-frame 'finger curl ratio' into fist-closed state and fire events.

    ratio = mean(dist(fingertip, wrist) / dist(finger MCP, wrist)) over four fingers.
    An open hand is ~1.7-2.0, a fist ~0.7-1.1.

    Swing detection: instead of waiting for the hand to cross fixed "fully open" /
    "fully closed" lines (which fast flicks and low camera frame rates never complete),
    it fires on the *swing* of the curl relative to a short-term baseline:

      * open  -> a drop of ``swing`` below the baseline that ends in the lower part of
        the range is a close                                      -> FIRE
      * closed -> a rise of ``swing`` above the baseline (or reaching the clearly-open
        zone) re-arms the next shot.

    A partial re-open between shots is therefore enough, and a single camera frame of
    "open" still counts.  ``swing`` adapts to the measured frame-to-frame jitter (median
    based, so deliberate flicks don't inflate it) so a noisy fist cannot re-arm and fire
    by itself.  It also remembers where the palm was a moment *before* the fist closed so
    the shot lands where the player was aiming (closing a fist drags the palm).
    """

    JIT_WINDOW = 24

    def __init__(self, open_ref: float = 1.80, closed_ref: float = 0.90,
                 lock_delay: float = C.AIM_LOCK_DELAY, min_gap: float = C.FIST_MIN_GAP,
                 min_span: float = 0.35):
        self.lock_delay, self.min_gap = lock_delay, min_gap
        self.min_span = min_span          # smallest open-closed separation calibration accepts
        self._set_refs(open_ref, closed_ref)
        self._hist: deque = deque()
        self._dabs: deque = deque(maxlen=self.JIT_WINDOW)
        self.reset()

    def _set_refs(self, open_ref, closed_ref):
        self.open_ref, self.closed_ref = open_ref, closed_ref
        span = max(open_ref - closed_ref, 0.2)
        self.base_swing = max(0.12, C.FIST_SWING * span)    # swing needed on a clean signal
        self.max_swing = 0.6 * span
        self.gate_hi = closed_ref + C.FIST_GATE * span      # a close must end below this
        self.sure_lo = closed_ref + 0.22 * span             # unmistakably a fist
        self.open_gate = closed_ref + 0.75 * span           # unmistakably open

    # kept for the debug overlay / calibration display
    @property
    def close_thr(self):
        return self.gate_hi

    @property
    def open_thr(self):
        return self.open_gate

    @property
    def delta(self):
        return self.base_swing

    @property
    def swing(self) -> float:
        """Current swing threshold: the configured one, but never less than ~4.5x the
        measured jitter (median of recent frame-to-frame changes)."""
        if len(self._dabs) < 6:
            return self.base_swing
        jit = sorted(self._dabs)[len(self._dabs) // 2]
        return min(max(self.base_swing, 4.5 * jit), self.max_swing)

    def reset(self):
        self.closed = False
        self._base = None            # short-term baseline of the ratio
        self._last_t = None
        self._last_fire = -1e9
        self._prev = None
        self.ratio = None
        self._hist.clear()
        self._dabs.clear()

    def calibrate(self, open_ratio: float, closed_ratio: float) -> bool:
        """Set reference values from measured ones. Returns False (and keeps the
        defaults) if the two measurements are too close to be separable."""
        if open_ratio - closed_ratio < self.min_span:
            return False
        self._set_refs(open_ratio, closed_ratio)
        return True

    def _pos_at(self, t):
        for ts, x, y in reversed(self._hist):
            if ts <= t:
                return x, y
        return self._hist[0][1], self._hist[0][2]

    def update(self, t: float, ratio: float, x: float, y: float):
        """Returns (closed, fired, (fire_x, fire_y))."""
        self._hist.append((t, x, y))
        while self._hist and t - self._hist[0][0] > 0.6:
            self._hist.popleft()
        r = ratio
        self.ratio = r
        dt = 0.0 if self._last_t is None else max(0.0, t - self._last_t)
        self._last_t = t
        if self._prev is not None:
            self._dabs.append(abs(r - self._prev))
        self._prev = r

        if self._base is None:                     # first sample: start from absolute state
            self._base = r
            self.closed = r < self.gate_hi
            return self.closed, False, (x, y)

        swing = self.swing
        # EMA baseline (~0.3 s): follows the held pose but not a sudden flick
        a = 1.0 if dt <= 0 else min(1.0, dt / 0.30)
        fired = False
        if not self.closed:
            dropped = r < self._base - swing and r < self.gate_hi
            sure = r < self.sure_lo and r < self._base - 0.5 * swing
            if dropped or sure:
                if t - self._last_fire >= self.min_gap:
                    fired = True
                    self._last_fire = t
                self.closed = True
                self._base = r
            else:
                self._base += (r - self._base) * a
        else:
            if r > self._base + swing or r > self.open_gate:
                self.closed = False
                self._base = r
            else:
                self._base += (r - self._base) * a
        fire_pos = self._pos_at(t - self.lock_delay) if fired else (x, y)
        return self.closed, fired, fire_pos

    def lost(self, t: float):
        """Called when tracking drops out. MediaPipe tends to lose the hand exactly when it
        becomes a tight fist, so a hand that vanishes *mid-curl* counts as a close.
        A clearly open hand that vanishes (left the frame, occluded) does not.
        Returns (closed, fired, fire_pos)."""
        if self.closed or self.ratio is None or self._base is None or not self._hist:
            return self.closed, False, (0.0, 0.0)
        curling = (self.ratio < self._base - 0.5 * self.swing) and self.ratio < self.open_gate
        if curling and t - self._last_fire >= self.min_gap:
            self._last_fire = t
            self.closed = True
            self._base = self.ratio
            return True, True, self._pos_at(self._hist[-1][0] - self.lock_delay)
        return self.closed, False, (0.0, 0.0)


class LeadPredictor:
    """Latency compensation: extrapolates a smoothed 2D position a few tens of milliseconds
    ahead along its velocity, so the reticle sits where the hand *is* now rather than where
    the camera saw it ~100 ms ago.  The lead fades to zero when the hand is (nearly) still,
    so it never amplifies jitter."""

    def __init__(self, lead: float = C.AIM_LEAD, max_lead: float = C.AIM_LEAD_MAX):
        self.lead, self.max_lead = lead, max_lead
        self.reset()

    def reset(self):
        self._p = None
        self._t = None
        self.vx = self.vy = 0.0

    def __call__(self, t: float, x: float, y: float):
        if self.lead <= 0:
            return x, y
        if self._p is None:
            self._p, self._t = (x, y), t
            return x, y
        dt = max(t - self._t, 1e-3)
        vx = (x - self._p[0]) / dt
        vy = (y - self._p[1]) / dt
        a = min(1.0, dt / 0.06)
        self.vx += (vx - self.vx) * a
        self.vy += (vy - self.vy) * a
        self._p, self._t = (x, y), t
        speed = math.hypot(self.vx, self.vy)
        gain = min(1.0, max(0.0, (speed - 0.06) / 0.25))
        dx, dy = self.vx * self.lead * gain, self.vy * self.lead * gain
        mag = math.hypot(dx, dy)
        if mag > self.max_lead:
            dx, dy = dx * self.max_lead / mag, dy * self.max_lead / mag
        return min(1.0, max(0.0, x + dx)), min(1.0, max(0.0, y + dy))


class RefAdapter:
    """Learns this player's open / closed levels while they play, so nobody has to run a
    calibration step.  It only nudges the detector's reference values (30 % per update, clamped
    to plausible ranges), and only when the signal clearly shows both states - a hand that just
    aims never changes anything."""

    def __init__(self, det: "FistDetector", open_range, closed_range, every: int = 45, window: int = 900):
        self.det = det
        self.open_range, self.closed_range = open_range, closed_range
        self.every = every
        self.buf: deque = deque(maxlen=window)
        self.n = 0

    def feed(self, r: float, closed: bool) -> bool:
        """Returns True when the references were updated."""
        self.buf.append(r)
        self.n += 1
        if self.n % self.every or len(self.buf) < 150 or closed:
            return False
        vals = sorted(self.buf)
        last = len(vals) - 1
        lo, hi = vals[int(0.05 * last)], vals[int(0.95 * last)]
        if hi - lo < self.det.min_span * 1.4:
            return False
        mid = (lo + hi) / 2.0
        low = [v for v in vals if v < mid]
        high = [v for v in vals if v >= mid]
        frac_low = len(low) / len(vals)
        if not (0.06 <= frac_low <= 0.75) or len(low) < 12 or len(high) < 12:
            return False
        new_closed = low[len(low) // 2]                      # typical closed level
        new_open = high[int(0.75 * (len(high) - 1))]         # typical (upper) open level
        o = min(max(0.7 * self.det.open_ref + 0.3 * new_open, self.open_range[0]), self.open_range[1])
        c = min(max(0.7 * self.det.closed_ref + 0.3 * new_closed, self.closed_range[0]), self.closed_range[1])
        if o - c < self.det.min_span:
            return False
        self.det._set_refs(o, c)
        return True
