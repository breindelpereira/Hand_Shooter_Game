"""Analyse a recorded hand session and suggest detector settings.

    1. In the game press F8 to start recording, play (or just flick your fist / pinch a few
       times), press F8 again - a CSV is written to data/sessions/.
    2. python tools/analyze_session.py data/sessions/session_YYYYMMDD_HHMMSS.csv

The report shows how noisy the signal is, how often tracking was lost, the range of your
open/closed values, and re-runs the shot detector over the recording so you can see how many
shots the current settings (or ones you pass on the command line) would have fired.

    python tools/analyze_session.py session.csv --swing 0.22 --gate 0.7
"""
import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neural import config as C  # noqa: E402
from neural.filters import FistDetector  # noqa: E402
from neural.replay import load_session  # noqa: E402


def pct(vals, p):
    vals = sorted(vals)
    return vals[min(len(vals) - 1, max(0, int(p / 100 * (len(vals) - 1))))]


def jitter(vals):
    """Robust noise estimate: median absolute frame-to-frame change (~sigma for white noise)."""
    d = [abs(b - a) for a, b in zip(vals, vals[1:])]
    return statistics.median(d) if d else 0.0


def replay_detector(rows, key, open_ref, closed_ref, min_span):
    det = FistDetector(open_ref=open_ref, closed_ref=closed_ref, min_span=min_span)
    fires, lost_fires, was_present = 0, 0, False
    for r in rows:
        if r["present"] and r[key] is not None:
            _, f, _ = det.update(r["t"], r[key], r["x"], r["y"])
            fires += f
            was_present = True
        elif was_present:
            _, f, _ = det.lost(r["t"])
            lost_fires += f
            was_present = False
    return fires, lost_fires


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("--swing", type=float, help="override FIST_SWING for the re-run")
    ap.add_argument("--gate", type=float, help="override FIST_GATE for the re-run")
    a = ap.parse_args()
    if a.swing is not None:
        C.FIST_SWING = a.swing
    if a.gate is not None:
        C.FIST_GATE = a.gate

    meta, rows = load_session(a.session)
    if len(rows) < 10:
        print("session too short")
        return 1
    dur = rows[-1]["t"] - rows[0]["t"]
    dts = [b["t"] - a_["t"] for a_, b in zip(rows, rows[1:]) if b["t"] > a_["t"]]
    fps = 1.0 / statistics.median(dts) if dts else 0.0
    pres = [r for r in rows if r["present"]]
    gaps, run = 0, 0
    for r in rows:
        if r["present"]:
            run = 0
        else:
            run += 1
            if run == 1:
                gaps += 1
    print(f"session   : {a.session}")
    print(f"gesture   : {meta.get('gesture', '?')}   recorded shots: {sum(r['fired'] for r in rows)}")
    print(f"duration  : {dur:.1f} s   camera rate: {fps:.1f} FPS   frames: {len(rows)}")
    print(f"tracking  : hand present {100 * len(pres) / len(rows):.0f}% of frames, "
          f"{gaps} dropouts")
    if fps < 18:
        print("            ! low camera frame rate - light the room; fast flicks need >= 20 FPS")
    print()

    for key, nice, op_def, cl_def, span in (("fist", "FIST (finger curl)", 1.80, 0.90, 0.35),
                                            ("pinch", "PINCH (thumb-index)", 0.85, 0.20, 0.25)):
        vals = [r[key] for r in pres if r[key] is not None]
        if len(vals) < 10:
            continue
        o_ref = meta.get(f"{key}_open", op_def)
        c_ref = meta.get(f"{key}_closed", cl_def)
        p5, p50, p95 = pct(vals, 5), pct(vals, 50), pct(vals, 95)
        jit = jitter(vals)
        print(f"{nice}")
        print(f"  value range  : p5 {p5:.2f}   median {p50:.2f}   p95 {p95:.2f}"
              f"      (calibrated open {o_ref:.2f} / closed {c_ref:.2f})")
        print(f"  jitter       : {jit:.3f} per frame  -> swing needed on this signal ~ {4.5 * jit:.2f}"
              f"  (current base {C.FIST_SWING * (o_ref - c_ref):.2f})")
        fires, lost_fires = replay_detector(rows, key, o_ref, c_ref, span)
        print(f"  detector re-run: {fires} shots from the signal + {lost_fires} from tracking loss "
              f"(swing={C.FIST_SWING}, gate={C.FIST_GATE})")
        suggested_open, suggested_closed = p95, p5
        if suggested_open - suggested_closed >= span:
            print(f"  suggestion   : your real range looks like open ~{suggested_open:.2f} / "
                  f"closed ~{suggested_closed:.2f} - recalibrate if those differ a lot from the above")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
