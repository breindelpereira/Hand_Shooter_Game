"""Application shell: window, main loop, input selection, global keys, overlays."""
from __future__ import annotations

import json
import math

import pygame

from . import config as C
from .audio import Audio
from .fx import draw_text, scale_color
from .scenery import PINK_THEME, Tunnel
from .inputs import InputProvider, InputState, MouseInput
from .scenes import CalibrateScene, MenuScene, Scene


class Settings:
    """Run configuration + the options that persist between launches."""

    PATH = C.DATA_DIR / "settings.json"

    def __init__(self, duration=C.TIMED_DURATION, timed_lives=C.TIMED_LIVES, autofire=None,
                 gesture=None, fullscreen=None):
        self.duration = duration
        self.timed_lives = timed_lives
        self.autofire = False
        self.gesture = C.DEFAULT_GESTURE
        self.tutorial_done = False
        self.show_preview = True          # camera picture-in-picture
        self.fullscreen = True            # full screen by default; remembered
        try:
            with open(self.PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.autofire = bool(d.get("autofire", False))
            self.gesture = d.get("gesture", self.gesture)
            self.tutorial_done = bool(d.get("tutorial_done", False))
            self.fullscreen = bool(d.get("fullscreen", True))
            self.show_preview = bool(d.get("show_preview", True))
        except (OSError, ValueError):
            pass
        if fullscreen is not None:
            self.fullscreen = fullscreen
        if autofire is not None:
            self.autofire = autofire
        if gesture in ("click", "clock"):          # friendly names for the pinch gesture
            gesture = "pinch"
        if gesture in ("fist", "pinch"):
            self.gesture = gesture
        if self.gesture not in ("fist", "pinch"):
            self.gesture = "fist"

    def save(self):
        try:
            C.DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(self.PATH, "w", encoding="utf-8") as f:
                json.dump({"autofire": self.autofire, "gesture": self.gesture,
                           "tutorial_done": self.tutorial_done,
                           "fullscreen": self.fullscreen,
                           "show_preview": self.show_preview}, f)
        except OSError:
            pass


class App:
    def __init__(self, input_mode="auto", fullscreen=None, audio=True, camera=C.CAMERA_INDEX,
                 duration=C.TIMED_DURATION, timed_lives=C.TIMED_LIVES, bloom=True,
                 skip_calibration=False, autofire=None, gesture=None, replay=None):
        pygame.init()
        self.cfg = Settings(duration, timed_lives, autofire, gesture, fullscreen)
        self._open_display(self.cfg.fullscreen)
        pygame.display.set_caption("Neural Targeting")
        pygame.mouse.set_visible(False)
        self.canvas = pygame.Surface((C.W, C.H)).convert()
        self.clock = pygame.time.Clock()
        self.clock_t = 0.0
        self.running = True
        self._replay_path = replay
        self.bloom_on = bloom
        self.debug = False
        self.show_pip = self.cfg.show_preview
        self.session_trial_done = False   # reset by the idle timeout: the next visitor gets the trial
        self.idle_enabled = None          # None = auto (only when a camera is the input)
        self._last_hand = 0.0
        self.inp2 = InputState(present=False)
        self.players = 1                  # players in the CURRENT game (1 or 2)
        self.want_players = 1             # what the choose screen has selected
        self.toast = None            # [text, ttl, color]
        self._fire_flash = -10.0
        self._next: Scene | None = None
        self.scene: Scene | None = None

        self.audio = Audio(enabled=audio)
        self.tunnel = Tunnel()
        self.input: InputProvider = self._make_input(input_mode, camera)
        self._load_calibration()
        self._bl_mid = pygame.Surface((C.W // 2, C.H // 2)).convert()
        self._bl_small = pygame.Surface((C.W // 4, C.H // 4)).convert()
        self._bl_big = pygame.Surface((C.W, C.H)).convert()

        # No forced calibration or tutorial: the home screen is always first. The detector learns
        # the player's open/closed levels silently while they play; manual calibration (Settings)
        # still exists and overrides that.
        self.goto(MenuScene(self))
        self._swap()

    # ---- display ---------------------------------------------------------
    def _open_display(self, fullscreen: bool):
        """The game renders at 1280x720 and is scaled (letterboxed) to the window / desktop."""
        flags = pygame.SCALED | pygame.RESIZABLE
        if fullscreen:
            flags |= pygame.FULLSCREEN
        try:
            self.screen = pygame.display.set_mode((C.W, C.H), flags, vsync=1)
        except pygame.error:
            self.screen = pygame.display.set_mode((C.W, C.H), flags)

    def set_fullscreen(self, on: bool):
        """Switch with SDL's own toggle - re-creating the display with set_mode() crashes
        (access violation) when the SCALED window is already open."""
        if on == self.cfg.fullscreen:
            return
        try:
            pygame.display.toggle_fullscreen()
        except pygame.error:
            self.toast = ["could not switch full screen on this system", 3.0, C.ORANGE]
            return
        self.cfg.fullscreen = on
        self.cfg.save()

    def set_players(self, n: int):
        """Switch the tracker between one hand (centre of the view) and two (left / right halves)."""
        self.players = 2 if n >= 2 else 1
        self.input.set_players(self.players)

    def base_theme(self) -> int:
        """Clock mode (the pinch gesture) lives in the pink-purple world; fist mode uses the
        normal colour worlds, slowly cycling."""
        if self.cfg.gesture == "pinch":
            return PINK_THEME
        return int(self.clock_t / 9.0) % 5          # the menus drift through the colour worlds too

    def set_preview(self, on: bool):
        self.show_pip = on
        self.cfg.show_preview = on
        self.cfg.save()

    def toggle_fullscreen(self):
        self.set_fullscreen(not self.cfg.fullscreen)

    # ---- setup -----------------------------------------------------------
    def _make_input(self, mode: str, camera: int) -> InputProvider:
        if mode == "replay":
            from .replay import ReplayInput
            return ReplayInput(self._replay_path)
        if mode in ("hand", "auto"):
            try:
                from .hand import HandInput
                h = HandInput(camera, self.cfg.gesture)
                h.start()
                return h
            except Exception as exc:  # noqa: BLE001
                msg = f"Camera unavailable ({exc}) - using mouse"
                print("[input]", msg)
                self.toast = [msg, 6.0, C.ORANGE]
                if mode == "hand":
                    print("[input] --input hand requested but hand tracking failed")
        return MouseInput()

    def _read_calibration_file(self) -> dict:
        """{"fist": {"open", "closed"}, "pinch": {...}}; understands the old flat format."""
        try:
            with open(C.CALIB_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return {}
        if "open" in d and "closed" in d:
            d = {"fist": {"open": d["open"], "closed": d["closed"]}}
        return d if isinstance(d, dict) else {}

    def _load_calibration(self):
        self.calib: dict[str, bool] = {}
        self.calibrated = False
        if not self.input.has_camera:
            return
        for g, v in self._read_calibration_file().items():
            try:
                ok = bool(self.input.set_calibration(float(v["open"]), float(v["closed"]), g))
            except (KeyError, TypeError, ValueError):
                ok = False
            self.calib[g] = ok
        self.calibrated = self.calib.get(self.cfg.gesture, False)

    def is_calibrated(self, gesture: str | None = None) -> bool:
        return self.calib.get(gesture or self.cfg.gesture, False)

    def save_calibration(self, gesture: str, open_v: float, closed_v: float):
        d = self._read_calibration_file()
        d[gesture] = {"open": open_v, "closed": closed_v}
        try:
            C.DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(C.CALIB_PATH, "w", encoding="utf-8") as f:
                json.dump(d, f)
        except OSError:
            pass
        self.calib[gesture] = True

    def set_gesture(self, gesture: str):
        self.cfg.gesture = gesture
        self.cfg.save()
        if hasattr(self.input, "set_gesture"):
            self.input.set_gesture(gesture)

    # ---- scene management --------------------------------------------------
    def goto(self, scene: Scene):
        self._next = scene

    def _swap(self):
        if self._next is None:
            return
        if self.scene is not None:
            self.scene.leave()
        self.scene = self._next
        self._next = None
        self.scene.enter()

    # ---- helpers for scenes ------------------------------------------------
    def preview_surface(self, scale: float = 1.0):
        arr = self.input.preview()
        if arr is None:
            return None
        h, w = arr.shape[:2]
        surf = pygame.image.frombuffer(arr.tobytes(), (w, h), "RGB").convert()
        if scale != 1.0:
            surf = pygame.transform.smoothscale(surf, (int(w * scale), int(h * scale)))
        return surf

    def _bloom(self, canvas):
        """Cheap glow: cheap 2x down, area-average to 1/4, smooth up to 1/2, 2x up, add."""
        mid, small, big = self._bl_mid, self._bl_small, self._bl_big
        pygame.transform.scale(canvas, mid.get_size(), mid)
        pygame.transform.smoothscale(mid, small.get_size(), small)
        small.fill((120, 120, 120), special_flags=pygame.BLEND_RGB_MULT)
        pygame.transform.smoothscale(small, mid.get_size(), mid)
        pygame.transform.scale(mid, (C.W, C.H), big)
        canvas.blit(big, (0, 0), special_flags=pygame.BLEND_RGB_ADD)

    # ---- main loop ---------------------------------------------------------
    def run(self):
        try:
            while self.running:
                dt = min(self.clock.tick(C.FPS) / 1000.0, 0.05)
                self.clock_t += dt
                for e in pygame.event.get():
                    if e.type == pygame.QUIT:
                        self.running = False
                    elif e.type == pygame.KEYDOWN:
                        self._on_key(e.key)
                inps = self.input.poll_players(dt)
                inp = inps[0]
                self.inp2 = inps[1]                 # player 2 (absent unless a 2-player game runs)
                self._last_inp = inp
                if inp.fire:
                    self._fire_flash = self.clock_t
                if inp.present or self.inp2.present:
                    self._last_hand = self.clock_t
                self._check_idle()
                self.scene.update(dt, inp)
                self._swap()
                self.audio.update(dt)
                self._draw()
        finally:
            self.audio.music_stop()
            self.input.stop()
            pygame.quit()

    def _check_idle(self):
        """Walk-up kiosk behaviour: if nobody has been in front of the camera for IDLE_RESET
        seconds, drop whatever is on screen (a half-played game, someone's name entry) and go back
        to the attract screen, and make the next visitor get the quick trial again."""
        enabled = self.input.has_camera if self.idle_enabled is None else self.idle_enabled
        if not enabled or self._next is not None:
            return
        if self.clock_t - self._last_hand > C.IDLE_RESET:
            self.session_trial_done = False
            self.want_players = 1
            if not isinstance(self.scene, (MenuScene, CalibrateScene)):
                self.goto(MenuScene(self))

    def _on_key(self, key):
        # while a text field is open, letters / digits / space / backspace / enter belong to it
        if getattr(self.scene, "wants_text", False) and (
                pygame.K_a <= key <= pygame.K_z or pygame.K_0 <= key <= pygame.K_9
                or key in (pygame.K_SPACE, pygame.K_BACKSPACE, pygame.K_RETURN)):
            self.scene.on_key(key)
            return
        if key == pygame.K_F11:
            self.toggle_fullscreen()
        elif key == pygame.K_m:
            muted = self.audio.toggle_mute()
            self.toast = ["SOUND OFF" if muted else "SOUND ON", 1.2, C.GREY]
        elif key == pygame.K_b:
            self.bloom_on = not self.bloom_on
            self.toast = ["BLOOM " + ("ON" if self.bloom_on else "OFF"), 1.2, C.GREY]
        elif key == pygame.K_F3:
            self.debug = not self.debug
        elif key == pygame.K_c:
            self.set_preview(not self.show_pip)
        elif key == pygame.K_F8:
            self._toggle_recording()
        elif key == pygame.K_F4:
            self.running = False
        else:
            self.scene.on_key(key)

    def _toggle_recording(self):
        inp = self.input
        if not hasattr(inp, "start_recording"):
            self.toast = ["recording needs the camera input", 2.0, C.GREY]
            return
        if inp.recording:
            path = inp.stop_recording()
            self.toast = [f"session saved: {path.name}" if path else "nothing recorded", 4.0, C.GREEN]
        else:
            inp.start_recording()
            self.toast = ["RECORDING hand session - press F8 to stop", 3.0, C.RED]

    def _draw(self):
        sc = self.scene
        sc.draw(self.canvas)
        if self.bloom_on:
            self._bloom(self.canvas)
        self.screen.fill((0, 0, 0))
        self.screen.blit(self.canvas, sc.offset)
        sc.draw_overlay(self.screen)
        self._draw_pip()
        if self.toast:
            self.toast[1] -= 1 / 60
            if self.toast[1] <= 0:
                self.toast = None
            else:
                draw_text(self.screen, self.toast[0], 22, self.toast[2], (C.CX, C.H - 24),
                          glow=False, fade=min(1.0, self.toast[1] * 2))
        if getattr(self.input, "recording", False) and int(self.clock_t * 2) % 2 == 0:
            pygame.draw.circle(self.screen, C.RED, (C.W - 24, 24), 8)
            draw_text(self.screen, "REC", 14, C.RED, (C.W - 40, 24), "midright", glow=False)
        if self.debug:
            self._draw_debug()
        pygame.display.flip()

    def _draw_pip(self):
        """Camera picture-in-picture (bigger on the home / pick screens) + the 'step into the
        zone' guide shown there while no hand is in view."""
        if not self.input.has_camera:
            return
        sc = self.scene
        if isinstance(sc, CalibrateScene):
            return
        inp = getattr(self, "_last_inp", None)
        present = True if inp is None else inp.present
        guide = getattr(sc, "show_guide", False) and not present
        r = None
        if self.show_pip:
            surf = self.preview_surface(getattr(sc, "pip_scale", 0.75))
            if surf is not None:
                r = surf.get_rect(bottomright=(C.W - 14, C.H - 40))
                self.screen.blit(surf, r)
                fired = self.clock_t - self._fire_flash < 0.18
                if guide:
                    border = scale_color(C.ORANGE, 0.55 + 0.45 * math.sin(self.clock_t * 6))
                    pygame.draw.rect(self.screen, border, r, 5)
                else:
                    border = C.GOLD if fired else C.CYAN
                    pygame.draw.rect(self.screen, border, r, 4 if fired else 2)
                self._draw_fist_meter(r)
                lowlight = getattr(self.input, "brightness", 255) < 40
                cam_fps = getattr(self.input, "fps", 0.0)
                if lowlight or 0.0 < cam_fps < 20.0:
                    msg = f"CAMERA {cam_fps:.0f} FPS - ADD LIGHT" if cam_fps > 0 else "LOW LIGHT - ADD LIGHT"
                    dy = 64 if guide else 12
                    draw_text(self.screen, msg, 16, C.ORANGE, (r.centerx, r.top - dy), glow=False)
        if guide:
            p = 0.6 + 0.4 * math.sin(self.clock_t * 6)
            ax, ay = (r.centerx, r.top) if r else (C.W - 160, C.H - 120)
            draw_text(self.screen, "STEP IN - RAISE YOUR HAND IN THE BOX", 22, C.ORANGE, (ax, ay - 40),
                      glow=False, fade=p)
            draw_text(self.screen, "this is what the camera sees", 16, C.GREY, (ax, ay - 16), glow=False)
            bob = int(4 * math.sin(self.clock_t * 8))
            pygame.draw.polygon(self.screen, C.ORANGE, [(ax - 10, ay - 4 + bob), (ax + 10, ay - 4 + bob),
                                                        (ax, ay + 8 + bob)])

    def _draw_fist_meter(self, pip):
        """A bar under the camera preview: current finger-curl vs. the fire threshold."""
        meter = getattr(self.input, "meter", None)
        if meter is None:
            return
        ratio, closed_ref, gate_hi, open_ref, closed = meter()
        lo, hi = closed_ref - 0.25, open_ref + 0.3
        bar = pygame.Rect(pip.left, pip.bottom + 8, pip.width, 12)
        pygame.draw.rect(self.screen, (20, 28, 50), bar, border_radius=6)

        def px(v):
            return bar.left + int(bar.width * min(1.0, max(0.0, (v - lo) / (hi - lo))))
        gx = px(gate_hi)
        pygame.draw.rect(self.screen, (60, 40, 20), (bar.left, bar.top, gx - bar.left, bar.height),
                         border_radius=6)
        pygame.draw.line(self.screen, C.ORANGE, (gx, bar.top - 3), (gx, bar.bottom + 3), 2)
        col = C.GREEN if closed else C.WHITE
        pygame.draw.circle(self.screen, col, (px(ratio), bar.centery), 7)
        label = self.input.spec()["label"] if hasattr(self.input, "spec") else "FIST"
        draw_text(self.screen, label, 12, C.GREY, (bar.left, bar.bottom + 4), "topleft", glow=False)
        draw_text(self.screen, "OPEN", 12, C.GREY, (bar.right, bar.bottom + 4), "topright", glow=False)

    def _draw_debug(self):
        inp = getattr(self, "_last_inp", InputState())
        lines = [f"FPS {self.clock.get_fps():.0f}   input: {self.input.name}",
                 f"present {inp.present}  fist {inp.fist}  ratio {inp.ratio:.2f}",
                 f"reticle {inp.x:.0f},{inp.y:.0f}"]
        cam = getattr(self.input, "fps", None)
        if cam is not None:
            lines.append(f"camera FPS {cam:.0f}   brightness {getattr(self.input, 'brightness', 0):.0f}")
            lines.append("fist thr close<%.2f open>%.2f" % self.input.thresholds())
        for i, ln in enumerate(lines):
            draw_text(self.screen, ln, 18, C.GREEN, (12, 120 + i * 22), "topleft", glow=False)
