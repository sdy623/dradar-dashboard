"""Run from a checkout with `python radar.py` after installing dependencies."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from zhongce_radar.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
