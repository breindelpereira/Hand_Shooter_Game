import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neural import config as C
from neural.filters import FistDetector, OneEuro
from neural.modes import EndlessMode, TimedMode, level_spec
from neural.scoring import Scoring, rank_for
from neural.world import World


# ---- filters ---------------------------------------------------------------
def test_one_euro_reduces_jitter_but_follows_steps():
    rng = random.Random(1)
    f = OneEuro()
    t, outs, raws = 0.0, [], []
    for _ in range(90):                       # 3 s of a still hand with noise
        raw = 0.5 + rng.gauss(0, 0.004)
        raws.append(raw)
        outs.append(f(raw, t))
        t += 1 / 30
    jit_raw = max(raws[30:]) - min(raws[30:])
    jit_out = max(outs[30:]) - min(outs[30:])
    assert jit_out < jit_raw * 0.6
    for _ in range(10):                       # a fast move must be tracked within ~0.3 s
        outs.append(f(0.9, t))
        t += 1 / 30
    assert outs[-1] > 0.85


def _feed(det, ratios, x=0.5, y=0.5, dt=1 / 30, t0=0.0):
    out, t = [], t0
    for r in ratios:
        out.append(det.update(t, r, x, y))
        t += dt
    return out, t


def _fires(res):
    return sum(1 for _, f, _ in res if f)


def test_fist_fires_once_on_close_and_not_while_held():
    det = FistDetector()
    res, t = _feed(det, [1.8] * 10)
    assert _fires(res) == 0 and not det.closed
    res, t = _feed(det, [1.0] * 10, t0=t)
    assert _fires(res) == 1 and det.closed
    res, t = _feed(det, [1.0] * 10, t0=t)           # holding does not re-fire
    assert _fires(res) == 0
    res, t = _feed(det, [1.8] * 10, t0=t)
    assert not det.closed and _fires(res) == 0


def test_fast_flicks_each_fire_at_30fps():
    det = FistDetector()
    _, t = _feed(det, [1.8] * 6)
    res, t = _feed(det, ([1.8, 1.8, 0.9, 0.9] * 8), t0=t)   # 8 flicks, 0.13 s per cycle
    assert _fires(res) == 8


def test_fast_flicks_fire_at_10fps_even_if_hand_only_opens_one_frame():
    det = FistDetector()
    _, t = _feed(det, [1.8] * 3, dt=0.1)
    res, t = _feed(det, ([1.0, 1.8] * 8), dt=0.1, t0=t)      # closed / open on alternate frames
    assert _fires(res) == 8


def test_partial_reopen_between_shots_still_rearms():
    det = FistDetector()
    _, t = _feed(det, [1.8] * 4)
    res, t = _feed(det, [0.9, 0.9, 1.3, 1.3, 0.9, 0.9, 1.35, 0.95, 0.9, 1.3, 0.9], t0=t)
    assert _fires(res) == 4


def test_slow_close_fires_exactly_once():
    det = FistDetector()
    _, t = _feed(det, [1.8] * 5)
    res, t = _feed(det, [1.8 - 0.06 * i for i in range(16)], t0=t)   # 1.8 -> 0.9 over 0.5 s
    assert _fires(res) == 1


def test_noise_does_not_fire():
    rng = random.Random(4)
    det = FistDetector()
    res, t = _feed(det, [1.8 + rng.gauss(0, 0.07) for _ in range(150)])     # open hand, 5 s
    assert _fires(res) == 0
    res, t = _feed(det, [0.9] * 3, t0=t)                                    # one real close
    assert _fires(res) == 1
    res, t = _feed(det, [0.9 + rng.gauss(0, 0.06) for _ in range(150)], t0=t)  # held fist, 5 s
    assert _fires(res) == 0


def test_hand_lost_mid_curl_counts_as_a_close_but_open_hand_loss_does_not():
    det = FistDetector()
    _, t = _feed(det, [1.8] * 6)
    res, t = _feed(det, [1.55], t0=t)                  # last visible frame: fingers starting to curl
    assert _fires(res) == 0 and not det.closed
    closed, fired, pos = det.lost(t + 0.1)             # ... then the tracker loses the hand
    assert fired and closed

    det2 = FistDetector()
    _, t = _feed(det2, [1.8] * 6)                      # open hand leaves the frame
    closed, fired, _ = det2.lost(t + 0.1)
    assert not fired and not closed

    # an already-closed fist that vanishes stays closed (no new shot)
    det3 = FistDetector()
    _, t = _feed(det3, [1.8] * 3 + [0.8] * 3)
    closed, fired, _ = det3.lost(t + 0.1)
    assert closed and not fired


def test_noisy_held_fist_and_open_hand_never_self_fire_but_real_flicks_still_do():
    for sigma in (0.05, 0.09, 0.13):
        for base in (1.8, 0.75):
            for seed in range(3):
                rng = random.Random(seed)
                det = FistDetector()
                det.calibrate(1.86, 0.65)
                # real input is noisy from the first frame; give the jitter estimate 1 s
                _, t = _feed(det, [base + rng.gauss(0, sigma) for _ in range(30)])
                res, t = _feed(det, [base + rng.gauss(0, sigma) for _ in range(30 * 40)], t0=t)
                # strict at realistic jitter, tolerant at extreme (sigma 0.13 = a third of the span)
                assert _fires(res) <= (0 if sigma < 0.1 else 2), (sigma, base, seed, _fires(res))
    # with that same noise a deliberate full flick sequence is still all detected
    rng = random.Random(7)
    det = FistDetector()
    det.calibrate(1.86, 0.65)
    _, t = _feed(det, [1.8 + rng.gauss(0, 0.09) for _ in range(60)])
    seq = []
    for _ in range(6):
        seq += [0.7 + rng.gauss(0, 0.09)] * 3 + [1.7 + rng.gauss(0, 0.09)] * 3
    res, t = _feed(det, seq, t0=t)
    assert _fires(res) == 6, _fires(res)


def test_fist_aim_lock_uses_position_before_closing():
    det = FistDetector()
    t = 0.0
    for i in range(15):                       # hand resting at x=0.3
        det.update(t, 1.8, 0.3, 0.5)
        t += 1 / 30
    fire_pos = None
    for i in range(10):                       # fist closes while palm drifts right
        _, fired, pos = det.update(t, max(0.9, 1.8 - 0.3 * i), 0.3 + 0.03 * (i + 1), 0.5)
        if fired:
            fire_pos = pos
        t += 1 / 30
    assert fire_pos is not None
    assert abs(fire_pos[0] - 0.3) < 0.07      # near where the aim was, not the drifted spot


def test_calibration_rejects_inseparable_values_and_scales_swing():
    det = FistDetector()
    assert not det.calibrate(1.2, 1.1)
    assert det.calibrate(1.9, 0.7)
    assert det.closed_ref == 0.7 and det.gate_hi < det.open_gate
    # a user with a small range still gets a working detector
    small = FistDetector()
    assert small.calibrate(1.4, 0.8)
    _, t = _feed(small, [1.4] * 4)
    res, t = _feed(small, [0.85, 1.4, 0.85, 1.4, 0.85], t0=t)
    assert _fires(res) == 3


# ---- world -----------------------------------------------------------------
def test_target_leaks_when_it_reaches_player():
    w = World(random.Random(0))
    w.base_speed = 1.0
    t = w.spawn("standard", 0.0, 0.0, 10.0)
    leaks, steps = [], 0
    while not leaks and steps < 2000:
        leaks = w.update(1 / 60)
        steps += 1
    assert leaks and leaks[0][0] == "leak" and leaks[0][1] is t


def test_fire_hits_and_misses():
    w = World(random.Random(0))
    t = w.spawn("standard", 0.0, 0.0, 12.0)
    sx, sy, sr = w.project(t)
    assert w.fire(sx + 300, sy) == []
    hits = w.fire(sx, sy)
    assert len(hits) == 1 and hits[0].killed and hits[0].perfect
    assert w.targets == []


def test_shielded_needs_two_hits_and_splitter_splits():
    w = World(random.Random(0))
    t = w.spawn("shielded", 0, 0, 12)
    sx, sy, _ = w.project(t)
    assert not w.fire(sx, sy)[0].killed
    assert w.fire(sx, sy)[0].killed
    s = w.spawn("splitter", 0.5, 0, 12)
    sx, sy, _ = w.project(s)
    w.fire(sx, sy)
    assert sum(1 for x in w.targets if x.kind == "mini") == 2


def test_closest_target_wins_overlap():
    w = World(random.Random(0))
    far = w.spawn("standard", 0, 0, 30)
    near = w.spawn("standard", 0, 0, 8)
    sx, sy, _ = w.project(near)
    hit = w.fire(sx, sy)[0]
    assert hit.target is near and far.alive


def test_blast_clears_mines_safely_and_boss_dies_when_nodes_do():
    w = World(random.Random(0))
    w.spawn("mine", 0.1, 0.1, 15)
    w.spawn("standard", -0.2, 0.0, 20)
    hits = w.blast(C.CX, C.CY, 400)
    assert len(hits) == 2 and all(h.blast for h in hits)
    b = w.spawn_boss(nodes=2)
    w.update(0.01)
    kills = []
    for n in list(b.children):
        sx, sy, _ = w.project(n)
        kills += w.fire(sx, sy)
        w.update(0.01)
    assert any(h.boss_down for h in kills) and not b.alive


def test_spread_fires_three_rays():
    w = World(random.Random(0))
    a = w.spawn("standard", 0, 0, 10)
    sx, sy, _ = w.project(a)
    left = w.spawn("standard", (-C.OVERCHARGE_SPREAD) / (C.FOCAL / 10), 0, 10)
    hits = w.fire(sx, sy, spread=C.OVERCHARGE_SPREAD)
    assert len(hits) == 2


def test_aim_picks_the_target_you_point_at_most_precisely():
    w = World(random.Random(0))
    near = w.spawn("standard", 0.9, 0.0, 6)           # big, close to the player, to the right
    far = w.spawn("small", 0.0, 0.0, 30)              # tiny, far, at the centre
    fsx, fsy, _ = w.project(far)
    # pointing at the tiny one's centre should hit it even if the near one's big halo covers it
    assert w.target_at(fsx, fsy) is far
    nsx, nsy, _ = w.project(near)
    assert w.target_at(nsx, nsy) is near


def test_auto_lock_needs_precise_aim_slightly_sticky_and_never_picks_mines():
    w = World(random.Random(0))
    t = w.spawn("small", 0.5, 0.2, 14)
    sx, sy, sr = w.project(t)
    cap = max(sr * C.AUTO_CAPTURE_SCALE + C.AUTO_CAPTURE_PAD, C.AUTO_CAPTURE_PX)
    assert cap < 40                                        # a tight zone, not the old ~60 px halo
    # you have to be ON the target: well outside the zone nothing locks ...
    assert w.lock_candidate(sx + cap * 1.8, sy) is None
    assert w.lock_candidate(sx + 45, sy) is None
    # ... and inside it locks
    assert w.lock_candidate(sx + cap * 0.8, sy) is t
    # a held lock is only a little sticky: small drift is forgiven, real drift releases
    assert w.lock_candidate(sx + cap * 1.15, sy) is None
    assert w.lock_candidate(sx + cap * 1.15, sy, current=t) is t
    assert w.lock_candidate(sx + cap * 1.8, sy, current=t) is None
    # mines are never locked, and a mine sitting on top of a target does not intercept
    m = w.spawn("mine", 0.5, 0.2, 14)
    assert w.lock_candidate(sx, sy) in (t, None)
    assert w.lock_candidate(sx, sy) is not m
    w2 = World(random.Random(0))
    mine = w2.spawn("mine", 0.0, 0.0, 10)
    msx, msy, _ = w2.project(mine)
    assert w2.lock_candidate(msx, msy) is None
    # sweeping needs time: the hold requirement is what stops "just wave your hand about"
    assert C.AUTO_LOCK_TIME >= 0.25


# ---- scoring ---------------------------------------------------------------
def test_combo_multiplier_and_reset():
    s = Scoring()
    for _ in range(5):
        s.kill("standard", 100, 0.0, False)
    assert s.multiplier == 2
    s.reset_combo()
    assert s.multiplier == 1 and s.max_combo == 5
    s.combo = 500
    assert s.multiplier == C.MAX_MULT


def test_charge_cell_every_n_kills():
    s = Scoring()
    cells = [s.kill("standard", 10, 0, False)[2] for _ in range(C.CHARGE_CELL_EVERY)]
    assert cells[-1] and s.charge == 1


def test_rank():
    s, a, b = 45000, 22000, 9000                 # scoring.RANK_SCORE["timed"]
    assert rank_for("timed", s, 0.8) == "S"
    assert rank_for("timed", s, 0.3) == "A"      # S needs decent accuracy too
    assert rank_for("timed", a, 0.9) == "A"
    assert rank_for("timed", b, 0.9) == "B"
    assert rank_for("timed", 100, 0.9) == "C"
    assert rank_for("endless", 0, 0.5, level=10) == "A"


# ---- modes -----------------------------------------------------------------
def _sim(mode_cls, seconds, skill=0.9, seed=3):
    from neural.inputs import BotInput
    rng = random.Random(seed)
    world, sc = World(rng), Scoring()
    mode = mode_cls(world, sc, rng)
    bot = BotInput(world, skill=skill, rng=random.Random(seed))
    t, dt, log = 0.0, 1 / 60, []
    while t < seconds and not mode.over:
        inp = bot.poll(dt)
        if mode.phase == "play" and inp.fire:
            hits = world.fire(inp.fire_x, inp.fire_y)
            sc.shot(bool(hits))
            for h in hits:
                if h.target.hazard:
                    mode.on_mine(h.target)
                elif h.killed:
                    sc.kill(h.target.kind, h.target.pts, h.target.z, h.perfect)
        for kind, tgt in world.update(dt):
            if kind == "leak":
                mode.on_leak(tgt)
            elif kind == "boss_leak":
                mode.on_boss_leak(tgt)
        mode.update(dt, True)
        log.extend(mode.pop_events())
        t += dt
    return mode, sc, log, t


def test_timed_mode_runs_countdown_then_60s_and_finishes():
    mode, sc, log, t = _sim(TimedMode, 100)
    names = [e[0] for e in log]
    assert names.count("tick") == 5 and "go" in names and "finished" in names
    assert mode.over
    # 5 s countdown + 60 s of play
    assert 64.5 < t < 66.5
    assert sc.kills > 20


def test_timed_mode_waits_for_hand():
    rng = random.Random(1)
    mode = TimedMode(World(rng), Scoring(), rng)
    for _ in range(300):
        mode.update(1 / 60, present=False)
    assert mode.phase == "countdown" and mode.cd == C.TIMED_COUNTDOWN


def test_endless_levels_progress_and_end_on_death():
    mode, sc, log, t = _sim(EndlessMode, 400, skill=0.55)
    names = [e[0] for e in log]
    assert "level_intro" in names
    assert mode.level >= 2
    assert mode.over or t >= 400


def test_level_spec_curve():
    assert level_spec(1)["quota"] == 6 and list(level_spec(1)["weights"]) == ["standard"]
    assert level_spec(5)["boss"] == "mini" and level_spec(10)["boss"] == "boss"
    assert "mine" in level_spec(6)["weights"]
    assert level_spec(30)["speed"] <= 1.95


# ---- persistence -----------------------------------------------------------
def test_high_scores_persist_and_rank_per_mode(tmp_path=None):
    import tempfile
    from neural import scoring as S
    d = Path(tempfile.mkdtemp())
    old_dir, old_path = C.DATA_DIR, C.SCORES_PATH
    C.DATA_DIR, C.SCORES_PATH = d, d / "scores.json"
    try:
        assert S.top_scores("timed") == []
        assert S.qualifies("timed", 100)
        S.save_score("timed", "AAA", 5000)
        S.save_score("timed", "BBB", 9000)
        S.save_score("endless", "CCC", 100, level=7)
        S.save_score("endless", "DDD", 9999, level=3)
        assert [e["name"] for e in S.top_scores("timed")] == ["BBB", "AAA"]
        # endless ranks by level first, then score
        assert [e["name"] for e in S.top_scores("endless")] == ["CCC", "DDD"]
        for i in range(12):
            S.save_score("timed", f"P{i:02d}", 1000 + i)
        assert len(S.top_scores("timed", 10)) == 10
        assert not S.qualifies("timed", 5)          # below the 10th best
        assert S.qualifies("timed", 99999)
    finally:
        C.DATA_DIR, C.SCORES_PATH = old_dir, old_path


def test_endless_clear_phase_gives_bonus_and_flawless_life():
    rng = random.Random(2)
    world, sc = World(rng), Scoring()
    mode = EndlessMode(world, sc, rng)
    for _ in range(600):                      # run countdown + intro
        mode.update(1 / 60, True)
        if mode.phase == "play":
            break
    assert mode.phase == "play"
    mode.queue.clear()                        # pretend every target was dealt with
    lives = mode.lives
    mode.update(1 / 60, True)
    assert mode.phase == "clear"
    info = mode.cleared_info
    assert info["flawless"] and info["life"] and mode.lives == lives + 1
    assert sc.score >= info["bonus"] > 0


# ---- pinch gesture + latency lead ------------------------------------------
def test_pinch_detector_fires_on_quick_pinches_and_ignores_noise():
    from neural.hand import make_detector
    det = make_detector("pinch")
    assert abs(det.open_ref - 0.85) < 1e-9 and det.min_span == 0.25
    _, t = _feed(det, [0.85] * 6)
    res, t = _feed(det, [0.18, 0.18, 0.8, 0.8] * 6, t0=t)          # 6 quick pinches at 30 fps
    assert _fires(res) == 6
    # a calibrated user with a small pinch range still works
    small = make_detector("pinch")
    assert small.calibrate(0.6, 0.25)
    _, t = _feed(small, [0.6] * 5)
    res, t = _feed(small, [0.28, 0.6, 0.28, 0.6], t0=t)
    assert _fires(res) == 2
    # noise on an open pinch signal never fires
    rng = random.Random(3)
    det2 = make_detector("pinch")
    _, t = _feed(det2, [0.85 + rng.gauss(0, 0.04) for _ in range(30)])
    res, t = _feed(det2, [0.85 + rng.gauss(0, 0.04) for _ in range(30 * 30)], t0=t)
    assert _fires(res) == 0


def _fake_hand(open_hand=True, pinch=False):
    """21 landmarks, wrist at (0.5, 0.8), fingers pointing up; crude but geometric."""
    from types import SimpleNamespace as P
    lm = [P(x=0.5, y=0.8) for _ in range(21)]
    lm[0] = P(x=0.5, y=0.8)
    mcp_y = 0.60                                    # palm length 0.2
    for k, (mcp, tip) in enumerate(zip((5, 9, 13, 17), (8, 12, 16, 20))):
        x = 0.44 + 0.04 * k
        lm[mcp] = P(x=x, y=mcp_y)
        lm[tip] = P(x=x, y=0.32 if open_hand else 0.66)     # extended vs curled back past the knuckle
    lm[4] = P(x=0.42, y=0.58)                                   # thumb tip
    if pinch:
        lm[8] = P(x=0.43, y=0.59)                               # index tip touching the thumb
    return lm


def test_gesture_signals_separate_open_fist_and_pinch():
    from neural.hand import curl_ratio, pinch_ratio
    op = _fake_hand(True)
    fist = _fake_hand(False)
    assert curl_ratio(op, 1.33) > 1.8 > 1.0 > curl_ratio(fist, 1.33) or curl_ratio(op, 1.33) > curl_ratio(fist, 1.33) + 0.8
    pin = _fake_hand(True, pinch=True)
    assert pinch_ratio(op, 1.33) > 0.5          # fingers apart
    assert pinch_ratio(pin, 1.33) < 0.15        # thumb and index touching
    # aspect-ratio correction: a wider camera frame must not change the answer much
    assert abs(pinch_ratio(pin, 1.0) - pinch_ratio(pin, 1.78)) < 0.15


def test_lead_predictor_leads_motion_but_not_stillness():
    from neural.filters import LeadPredictor
    lp = LeadPredictor(lead=0.06)
    t, x = 0.0, 0.2
    for _ in range(30):                         # steady 0.5 zone-units / second to the right
        px, py = lp(t, x, 0.5)
        t += 1 / 30
        x += 0.5 / 30
    assert px > x - 0.5 / 30 + 0.02             # ahead of the latest measurement
    assert px - x < 0.06                        # but bounded by lead * speed
    # a still hand with tiny noise must not be amplified
    rng = random.Random(5)
    lp2 = LeadPredictor(lead=0.06)
    worst = 0.0
    for i in range(300):
        nx = 0.5 + rng.gauss(0, 0.002)
        px, py = lp2(i / 30, nx, 0.5)
        worst = max(worst, abs(px - nx))
    assert worst < 0.004
    assert LeadPredictor(lead=0.0)(0.0, 0.3, 0.3) == (0.3, 0.3)


# ---- swoop / formations -------------------------------------------------------
def test_swoop_converges_to_lane_and_ring_spins_and_stays_in_range():
    w = World(random.Random(1))
    t = w.spawn("standard", 0.4, 0.2, C.Z_FAR, swoop=(20.0, -8.0))
    w.base_speed = 1.0
    w.update(0.0)
    assert abs(t.x - (0.4 + 20.0)) < 1e-6 and abs(t.y - (0.2 - 8.0)) < 1e-6      # starts far out
    steps = 0
    while t.z > t.sw_end + 0.5 and steps < 5000:
        w.update(1 / 60)
        steps += 1
    assert abs(t.x - 0.4) < 0.5 and abs(t.y - 0.2) < 0.25                          # arrived in its lane
    w2 = World(random.Random(2))
    ring = w2.spawn_formation(["standard"] * 6, "ring", 0.0, 0.0)
    assert len(ring) == 6
    w2.base_speed = 0.0
    w2.update(0.0)
    a0 = [(t.x, t.y) for t in ring]
    w2.update(0.5)
    a1 = [(t.x, t.y) for t in ring]
    assert any(abs(p[0] - q[0]) > 0.05 for p, q in zip(a0, a1))                 # it rotates
    assert all(abs(t.x) < 1.2 and abs(t.y) < 1.2 for t in ring)                     # within the lanes
    for shape, n in (("arc", 4), ("vee", 5)):
        assert len(World(random.Random(3)).spawn_formation(["small"] * n, shape)) == n


def test_modes_use_formations_and_swoops_during_a_run():
    mode, sc, log, t = _sim(TimedMode, 100, skill=0.9, seed=4)
    # rebuild the same run to look at what was spawned (formation members share a spawn z/lane)
    rng = random.Random(4)
    world, scoring = World(rng), Scoring()
    m = TimedMode(world, scoring, rng)
    swoops = rings = 0
    for _ in range(60 * 70):
        m.update(1 / 60, True)
        world.update(1 / 60)
        for tg in world.targets:
            if tg.age == 0.0 or tg.age < 0.02:
                swoops += 1 if (tg.sw_x or tg.sw_y) else 0
                rings += 1 if tg.ring_c is not None else 0
        if m.over:
            break
    assert swoops > 0 and rings > 0


# ---- session record / replay -----------------------------------------------------
def test_session_csv_roundtrip_and_replay_input(tmp_path=None):
    import tempfile
    from neural.replay import ReplayInput, load_session
    p = Path(tempfile.mkdtemp()) / "s.csv"
    p.write_text("# gesture=pinch fist_open=1.9 fist_closed=0.7 pinch_open=0.9 pinch_closed=0.2 swing=0.3\n"
                 "t,present,x,y,fist,pinch,closed,fired\n"
                 "0.0,1,0.5,0.5,1.8,0.9,0,0\n"
                 "0.1,1,0.6,0.5,1.7,0.8,0,0\n"
                 "0.2,1,0.6,0.5,0.9,0.15,1,1\n"
                 "0.3,0,0.6,0.5,,,0,0\n", encoding="utf-8")
    meta, rows = load_session(p)
    assert meta["gesture"] == "pinch" and abs(meta["pinch_closed"] - 0.2) < 1e-9
    assert len(rows) == 4 and rows[2]["fired"] == 1 and rows[3]["fist"] is None
    rp = ReplayInput(p, loop=False)
    fires, last = 0, None
    for _ in range(40):
        s = rp.poll(0.01)
        fires += s.fire
        last = s
    assert fires == 1
    assert abs(last.x - 0.6 * C.W) < 1e-6 and not last.present


def test_settings_persist_gesture_and_tutorial_flag():
    import tempfile
    from neural.app import Settings
    old = Settings.PATH
    Settings.PATH = Path(tempfile.mkdtemp()) / "settings.json"
    try:
        s = Settings()
        assert s.gesture == "fist" and not s.tutorial_done and not s.autofire
        s.gesture, s.autofire, s.tutorial_done = "pinch", True, True
        s.save()
        s2 = Settings()
        assert s2.gesture == "pinch" and s2.autofire and s2.tutorial_done
        assert Settings(gesture="fist").gesture == "fist"          # CLI override wins
    finally:
        Settings.PATH = old


def test_names_up_to_20_chars_are_stored_and_trimmed():
    import tempfile
    from neural import scoring as S
    d = Path(tempfile.mkdtemp())
    old_dir, old_path = C.DATA_DIR, C.SCORES_PATH
    C.DATA_DIR, C.SCORES_PATH = d, d / "scores.json"
    try:
        assert C.NAME_MAX == 20
        S.save_score("timed", "ABCDEFGHIJKLMNOPQRST", 100)          # exactly 20
        S.save_score("timed", "THIS NAME IS FAR TOO LONG TO KEEP", 90)
        S.save_score("timed", "   ", 80)                           # blank -> anonymous
        names = [e["name"] for e in S.top_scores("timed")]
        assert names[0] == "ABCDEFGHIJKLMNOPQRST" and len(names[1]) == 20 and names[2] == "---"
    finally:
        C.DATA_DIR, C.SCORES_PATH = old_dir, old_path


# ---- walk-up flow helpers ----------------------------------------------------------
def test_ref_adapter_learns_the_players_levels_but_ignores_pure_aiming():
    from neural.filters import RefAdapter
    rng = random.Random(8)
    det = FistDetector()
    ad = RefAdapter(det, (1.35, 2.5), (0.35, 1.25))
    for i in range(4000):                                   # ~15 % of the time the fist is closed
        closed_phase = (i % 105) >= 90
        r = (0.6 if closed_phase else 1.5) + rng.gauss(0, 0.04)
        ad.feed(r, closed=r < 1.0)
    assert abs(det.open_ref - 1.5) < 0.2 and abs(det.closed_ref - 0.6) < 0.2, (det.open_ref, det.closed_ref)
    det2 = FistDetector()
    ad2 = RefAdapter(det2, (1.35, 2.5), (0.35, 1.25))
    for i in range(4000):                                   # never closes: nothing to learn from
        ad2.feed(1.7 + rng.gauss(0, 0.04), closed=False)
    assert det2.open_ref == 1.80 and det2.closed_ref == 0.90


def test_todays_top_player_ignores_older_runs():
    import json
    import tempfile
    import time
    from neural import scoring as S
    d = Path(tempfile.mkdtemp())
    old_dir, old_path = C.DATA_DIR, C.SCORES_PATH
    C.DATA_DIR, C.SCORES_PATH = d, d / "scores.json"
    try:
        now = time.time()
        (d / "scores.json").write_text(json.dumps({
            "timed": [{"name": "OLD", "score": 90000, "when": now - 3 * 86400},
                      {"name": "TODAY", "score": 50000, "when": now}],
            "timed_easy": [{"name": "EZ", "score": 20000, "when": now}]}), encoding="utf-8")
        assert S.best_entry(("timed", "timed_easy"))["name"] == "OLD"
        assert S.best_entry(("timed", "timed_easy"), S.start_of_today())["name"] == "TODAY"
        assert S.best_entry(("endless",), S.start_of_today()) is None
    finally:
        C.DATA_DIR, C.SCORES_PATH = old_dir, old_path


def test_buttons_lean_in_pop_and_the_play_circle_needs_a_hold():
    from neural.inputs import InputState
    from neural.ui import BigButton, Button
    b = Button((500, 300, 200, 60), "X", "x")
    far = InputState(x=100, y=100, present=True)
    close = InputState(x=720, y=330, present=True)           # 20 px outside the right edge
    for _ in range(30):
        b.update(1 / 60, far)
    assert b.near < 0.05
    for _ in range(30):
        b.update(1 / 60, close)
    assert b.near > 0.6
    hit = InputState(x=600, y=330, present=True, fire=True, fire_x=600, fire_y=330)
    assert b.update(1 / 60, hit) and b.pop == 1.0
    big = BigButton((640, 450), 118, "PLAY", "play", dwell=1.0)
    on = InputState(x=650, y=460, present=True)
    fired = [big.update(1 / 60, on) for _ in range(40)]       # 0.67 s: not yet
    assert not any(fired)
    fired = [big.update(1 / 60, on) for _ in range(40)]       # now past 1 s
    assert any(fired)
    big2 = BigButton((640, 450), 118, "PLAY", "play", dwell=1.0)
    assert big2.update(1 / 60, InputState(x=640, y=450, present=True, fire=True, fire_x=640, fire_y=450))
    off = InputState(x=100, y=100, present=True, fire=True, fire_x=100, fire_y=100)
    assert not big2.update(1 / 60, off)                        # a fist elsewhere does not start it
