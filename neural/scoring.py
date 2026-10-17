"""Score, combo, multiplier, rank and persistent high scores."""
from __future__ import annotations

import json
import time

from . import config as C

CALLOUTS = {3: "TRIPLE", 5: "RAMPAGE", 10: "UNSTOPPABLE", 20: "GODLIKE", 35: "UNREAL"}

# Timed score thresholds for S / A / B. Bot simulations (tests/test_core._sim) score
# ~16k (sloppy) to ~70k (near-perfect) so these sit between "decent" and "excellent".
# Re-tune after human playtests: humans have aim jitter + camera latency.
RANK_SCORE = {"timed": (45000, 22000, 9000)}


class Scoring:
    def __init__(self):
        self.score = 0
        self.combo = 0
        self.max_combo = 0
        self.kills = 0
        self.leaks = 0
        self.shots = 0
        self.hits = 0
        self.perfects = 0
        self.mines_hit = 0
        self.by_kind: dict[str, int] = {}
        self.charge = 0                      # charge cells available
        self._kills_since_cell = 0

    @property
    def multiplier(self) -> int:
        return min(C.MAX_MULT, 1 + self.combo // C.COMBO_PER_STEP)

    @property
    def accuracy(self) -> float:
        return self.hits / self.shots if self.shots else 0.0

    def shot(self, hit_any: bool):
        self.shots += 1
        if hit_any:
            self.hits += 1

    def kill(self, kind: str, base: int, z: float, perfect: bool):
        """Register a kill. Returns (points, callout_or_None, new_charge_cell)."""
        self.combo += 1
        self.max_combo = max(self.max_combo, self.combo)
        self.kills += 1
        self.by_kind[kind] = self.by_kind.get(kind, 0) + 1
        dist_bonus = 1.0 + 0.5 * max(0.0, min(1.0, z / C.Z_FAR))   # early kills pay more
        pts = int(base * self.multiplier * dist_bonus)
        if perfect:
            pts = int(pts * 1.5)
            self.perfects += 1
        self.score += pts
        cell = False
        self._kills_since_cell += 1
        if self._kills_since_cell >= C.CHARGE_CELL_EVERY:
            self._kills_since_cell = 0
            if self.charge < C.CHARGE_CELLS_MAX:
                self.charge += 1
                cell = True
        return pts, CALLOUTS.get(self.combo), cell

    def add_points(self, pts: int):
        self.score = max(0, self.score + pts)

    def reset_combo(self):
        self.combo = 0


def rank_for(mode: str, score: int, accuracy: float, level: int = 0) -> str:
    if mode == "timed":
        s, a, b = RANK_SCORE["timed"]
        if score >= s and accuracy >= 0.6:
            return "S"
        if score >= a:
            return "A"
        if score >= b:
            return "B"
        return "C"
    # endless: by level reached
    if level >= 15:
        return "S"
    if level >= 10:
        return "A"
    if level >= 5:
        return "B"
    return "C"


# ---- persistent high scores ------------------------------------------------
def _sort_key(mode: str):
    if mode.startswith("endless"):               # "endless" and "endless_easy"
        return lambda e: (e.get("level", 0), e.get("score", 0))
    return lambda e: e.get("score", 0)


def load_scores() -> dict:
    try:
        with open(C.SCORES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def top_scores(mode: str, n: int = 10) -> list[dict]:
    entries = load_scores().get(mode, [])
    return sorted(entries, key=_sort_key(mode), reverse=True)[:n]


def qualifies(mode: str, score: int, level: int = 0, n: int = 10) -> bool:
    if score <= 0 and level <= 1:
        return False
    top = top_scores(mode, n)
    if len(top) < n:
        return True
    key = _sort_key(mode)
    return key({"score": score, "level": level}) > key(top[-1])


def save_score(mode: str, name: str, score: int, level: int = 0, accuracy: float = 0.0):
    data = load_scores()
    entries = data.setdefault(mode, [])
    name = (name or "---").strip()[:C.NAME_MAX] or "---"
    entries.append({"name": name, "score": int(score), "level": int(level),
                    "accuracy": round(accuracy, 3), "when": int(time.time())})
    entries.sort(key=_sort_key(mode), reverse=True)
    del entries[25:]
    try:
        C.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(C.SCORES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
    except OSError:
        pass


def start_of_today() -> float:
    lt = time.localtime()
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def best_entry(mode_keys, since: float | None = None):
    """Highest-scoring entry across the given tables (optionally only runs since a timestamp)."""
    best = None
    data = load_scores()
    for k in mode_keys:
        for e in data.get(k, []):
            if since is not None and e.get("when", 0) < since:
                continue
            if best is None or e.get("score", 0) > best.get("score", 0):
                best = e
    return best
