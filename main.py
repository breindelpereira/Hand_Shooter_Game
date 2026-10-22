"""Hand Shooter - hand-tracked arcade shooter.

    python main.py                 # hand tracking if a camera is found, else mouse
    python main.py --input mouse   # force mouse (development)
    python main.py --duration 70   # 70-second Score Attack
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def parse():
    p = argparse.ArgumentParser(description="Hand Shooter")
    p.add_argument("--input", choices=("auto", "hand", "mouse", "replay"), default="auto")
    p.add_argument("--replay", help="session CSV to play back with --input replay")
    p.add_argument("--gesture", choices=("fist", "pinch", "clock", "click"), default=None,
                   help="fire gesture (also a toggle in the menu; remembered)")
    p.add_argument("--camera", type=int, default=0, help="camera index")
    p.add_argument("--fullscreen", action="store_true", help="force full screen (the default)")
    p.add_argument("--windowed", action="store_true", help="start in a window (remembered)")
    p.add_argument("--no-audio", action="store_true")
    p.add_argument("--no-bloom", action="store_true")
    p.add_argument("--duration", type=float, default=60.0, help="Score Attack length in seconds")
    p.add_argument("--timed-lives", type=int, default=None,
                   help="give Score Attack N lives (default: none, points penalties instead)")
    p.add_argument("--skip-calibration", action="store_true")
    p.add_argument("--easy", action="store_true",
                   help="start with auto-fire on (aim only; can also be toggled in the menu)")
    return p.parse_args()


def main():
    a = parse()
    from neural.app import App
    fs = True if a.fullscreen else (False if a.windowed else None)
    App(input_mode=a.input, fullscreen=fs, audio=not a.no_audio, camera=a.camera,
        duration=a.duration, timed_lives=a.timed_lives, bloom=not a.no_bloom,
        skip_calibration=a.skip_calibration, autofire=True if a.easy else None,
        gesture=a.gesture, replay=a.replay).run()


if __name__ == "__main__":
    main()
