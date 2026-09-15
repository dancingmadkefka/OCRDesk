"""``python -m ocrgrade <command> [args]`` entry point. See ocrgrade/cli.py."""
from __future__ import annotations

import sys

from ocrgrade.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
