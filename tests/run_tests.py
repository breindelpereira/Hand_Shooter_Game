"""Dependency-free test runner:  python tests/run_tests.py   (pytest also works)."""
import importlib
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))


def main() -> int:
    failed = 0
    total = 0
    for path in sorted(HERE.glob("test_*.py")):
        mod = importlib.import_module(path.stem)
        for name in dir(mod):
            if not name.startswith("test_"):
                continue
            fn = getattr(mod, name)
            if not callable(fn):
                continue
            total += 1
            t0 = time.perf_counter()
            try:
                fn()
                print(f"PASS  {path.stem}.{name}  ({time.perf_counter() - t0:.2f}s)")
            except Exception:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {path.stem}.{name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
