"""Two-player modes: Duel fairness, Versus / Co-op scoring and flow, the choose screen."""
import os
import random
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neural import config as C  # noqa: E402

_tmp = Path(tempfile.mkdtemp())
C.DATA_DIR, C.SCORES_PATH, C.CALIB_PATH, C.STATS_PATH = (
    _tmp, _tmp / "scores.json", _tmp / "calibration.json", _tmp / "stats.json")

from neural.app import App, Settings  # noqa: E402
Settings.PATH = _tmp / "settings.json"
from neural.inputs import BotInput, InputProvider  # noqa: E402
from neural.scenes import PickScene, PlayScene, ResultsScene  # noqa: E402
from neural.versus import DuelFlow, DuelInterlude, VersusScene  # noqa: E402

DT = 1 / 60
_app = None


def app():
    global _app
    if _app is None:
        _app = App(input_mode="mouse", audio=False, skip_calibration=True, fullscreen=False)
    return _app


class SecondBot(BotInput):
    """Aims at the SECOND-nearest target so two bots do not fight over the same one."""

    def _pick(self):
        ts = sorted((t for t in self.world.targets
                     if t.alive and t.kind not in ("boss", "mine") and 6 < t.z < 34), key=lambda t: t.z)
        return ts[1] if len(ts) > 1 else (ts[0] if ts else None)


class DualBots(InputProvider):
    name = "dualbots"
    has_camera = False

    def __init__(self, world, skill1=0.95, skill2=0.55):
        self.b1 = BotInput(world, skill=skill1, rng=random.Random(1))
        self.b2 = SecondBot(world, skill=skill2, rng=random.Random(2))

    def poll(self, dt):
        return self.b1.poll(dt)

    def poll_players(self, dt):
        return [self.b1.poll(dt), self.b2.poll(dt)]


def _run_scene_until_results(a, scene, seconds=100):
    a.goto(scene)
    a._swap()
    a.input = DualBots(scene.world)
    a.input.set_players(2)
    for _ in range(int(seconds / DT)):
        inps = a.input.poll_players(DT)
        a.inp2 = inps[1]
        a.scene.update(DT, inps[0])
        a._swap()
        if isinstance(a.scene, ResultsScene):
            return True
    return False


def test_duel_turns_see_identical_targets_whatever_the_player_does():
    a = app()
    logs = []
    for skill in (0.25, 0.97):
        a.cfg.autofire = False
        pl = PlayScene(a, "timed", seed=424242, adaptive=False)
        a.goto(pl)
        a._swap()
        pl.mode.phase = "play"
        log = []
        orig = pl.world.spawn

        def spy(kind, x=None, y=None, z=C.Z_FAR, swoop=None, _o=orig, _l=log, _pl=pl):
            t = _o(kind, x, y, z, swoop=swoop)
            if kind != "mini":                       # splitter children depend on what you shot
                _l.append((round(_pl.mode.t, 2), kind, round(t.x0, 3), round(t.y0, 3)))
            return t
        pl.world.spawn = spy
        bot = BotInput(pl.world, skill=skill, rng=random.Random(5))
        for _ in range(int(45 / DT)):
            pl.update(DT, bot.poll(DT))
        logs.append(log)
    assert len(logs[0]) > 15
    assert logs[0] == logs[1], "both Duel turns must get exactly the same targets"


def test_duel_flow_compares_scores_and_hands_over_to_player_two():
    a = app()
    flow = DuelFlow(a)
    a.cfg.autofire = False
    flow.stats = [dict(score=30000, accuracy=0.8, max_combo=20, kills=40, leaks=5),
                  dict(score=45000, accuracy=0.7, max_combo=25, kills=55, leaks=3)]
    st = flow._final_stats()
    assert st["title"] == "PLAYER 2 WINS!" and st["score"] == 45000 and st["table"] == "duel"
    assert st["rank"] == "P2" and any(r[0] == "WINNER" and r[1] == "PLAYER 2" for r in st["rows"])
    flow.stats[1]["score"] = 30000
    assert "TIE" in flow._final_stats()["title"]
    flow.stats = []
    flow._turn_done(1, dict(score=1000, accuracy=0.5, max_combo=3, kills=5, leaks=0))
    a._swap()
    assert isinstance(a.scene, DuelInterlude) and a.scene.n == 2


def test_versus_credits_the_shooter_and_ends_in_a_results_screen():
    a = app()
    a.cfg.autofire = False
    scene = VersusScene(a, coop=False)
    assert _run_scene_until_results(a, scene), "a 60 s match must end on the results screen"
    p1, p2 = scene.p
    assert p1.sc.kills > 0 and p2.sc.kills > 0
    assert p1.sc.kills + p2.sc.kills == scene.team.kills
    st = a.scene.s
    assert st["table"] == "versus" and len(st["rows"]) == 5
    assert st["score"] == max(p1.sc.score, p2.sc.score)
    assert a.players == 1, "leaving the match returns the tracker to one player"


def test_mine_penalty_hits_only_the_shooter_in_versus_but_the_team_in_coop():
    a = app()
    a.cfg.autofire = False
    for coop in (False, True):
        scene = VersusScene(a, coop=coop)
        a.goto(scene)
        a._swap()
        scene.mode.phase = "play"
        p1, p2 = scene.p
        p1.sc.score = p2.sc.score = scene.team.score = 5000
        m = scene.world.spawn("mine", 0.3, 0.1, 14.0)
        sx, sy, _ = scene.world.project(m)
        scene._shoot(p2, sx, sy)
        assert p2.sc.mines_hit == 1 and p1.sc.mines_hit == 0
        if coop:
            assert scene.team.score == 5000 - C.TIMED_MINE_PENALTY
        else:
            assert p2.sc.score == 5000 - C.TIMED_MINE_PENALTY and p1.sc.score == 5000
            assert scene.team.score == 5000


def test_coop_kills_feed_one_shared_score_and_shared_combo():
    a = app()
    a.cfg.autofire = False
    scene = VersusScene(a, coop=True)
    a.goto(scene)
    a._swap()
    scene.mode.phase = "play"
    p1, p2 = scene.p
    for p in (p1, p2, p1, p2, p1, p2):
        t = scene.world.spawn("standard", 0.2, 0.1, 14.0)
        sx, sy, _ = scene.world.project(t)
        scene._shoot(p, sx, sy)
    assert scene.team.kills == 6 and scene.team.combo == 6
    assert p1.sc.kills == 3 and p2.sc.kills == 3
    assert scene.team.score > 0 and scene.score_of(p1) == scene.score_of(p2) == scene.team.score


def test_choose_screen_switches_between_solo_and_two_player_cards():
    a = app()
    a.want_players = 1
    pick = PickScene(a)
    assert [b.action for b in pick.modes] == ["timed", "endless"]
    a.want_players = 2
    pick._sync()
    assert [b.action for b in pick.modes] == ["versus", "coop", "duel"]
    assert pick.players[1].selected and not pick.players[0].selected
    a.want_players = 1
    pick._sync()
    assert [b.action for b in pick.modes] == ["timed", "endless"]


def test_player2_on_the_keyboard_input_and_absent_by_default():
    from neural.inputs import MouseInput
    m = MouseInput()
    p1, p2 = m.poll_players(DT)
    assert not p2.present                      # one player: player 2 absent
    m.set_players(2)
    p1, p2 = m.poll_players(DT)
    assert p2.present and 0 <= p2.x <= C.W


# ---- UI fixes ------------------------------------------------------------------------------
def test_letters_type_into_the_name_box_instead_of_triggering_hotkeys():
    import pygame
    a = app()
    stats = dict(mode="timed", table="timed", easy=False, score=5000, accuracy=0.5, max_combo=3, kills=9,
                 leaks=1, level=0, by_kind={}, perfects=0, lives_end=None, rank="B")
    a.goto(ResultsScene(a, stats))
    a._swap()
    res = a.scene
    assert res.entering and res.wants_text
    pip0, bloom0, muted0 = a.show_pip, a.bloom_on, a.audio.muted
    for ch in "CMBA":                                    # C = camera, M = mute, B = bloom hotkeys
        a._on_key(getattr(pygame, "K_" + ch.lower()))
    assert res.name == "CMBA", res.name
    assert (a.show_pip, a.bloom_on, a.audio.muted) == (pip0, bloom0, muted0), "hotkeys must not fire"
    a._on_key(pygame.K_F3)                               # function keys still work while typing
    assert a.debug is True
    a._on_key(pygame.K_F3)
    # once the score is saved the hotkeys come back
    res._save("CMBA")
    assert not res.wants_text
    a._on_key(pygame.K_c)
    assert a.show_pip != pip0
    a.set_preview(pip0)


def test_button_text_always_fits_inside_its_box():
    from neural.fx import font
    from neural.scenes import ScoresScene
    from neural.ui import Button, fit_size
    a = app()
    sc = ScoresScene(a)
    for easy in (False, True):
        for pair in (False, True):
            sc.easy, sc.pair = easy, pair
            sc._sync_tab()
            for b in (sc.tab, sc.who, sc.back):
                size = fit_size(b.label, b.size, b.rect.w - 28)
                assert font(size).size(b.label)[0] <= b.rect.w - 28 or size == 12, (b.label, size)
    b = Button((0, 0, 200, 50), "A LONG BUTTON LABEL", "x", size=34)
    assert font(fit_size(b.label, b.size, b.rect.w - 28)).size(b.label)[0] <= 172


def test_the_pointer_is_drawn_and_tracked_on_the_pause_screen():
    import neural.scenes as scenes
    from neural.inputs import InputState
    a = app()
    a.cfg.autofire = False
    pl = PlayScene(a, "timed")
    a.goto(pl)
    a._swap()
    pl.mode.phase = "play"
    pl.on_key(__import__("pygame").K_p)                  # pause
    assert pl.paused
    pl.update(DT, InputState(x=321, y=412, present=True))
    assert (pl.rx, pl.ry) == (321, 412), "the pointer must keep following the hand while paused"
    calls = []
    orig = scenes.draw_reticle
    scenes.draw_reticle = lambda *args, **kw: (calls.append(args[1:3]), orig(*args, **kw))[1]
    try:
        pl.draw(a.canvas)
    finally:
        scenes.draw_reticle = orig
    assert (321, 412) in calls[-1:], "pause screen must draw the pointer last, on top of the veil"
