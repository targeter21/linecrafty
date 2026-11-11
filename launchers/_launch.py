"""Shared by the launcher scripts next to it: run linecrafty with preset options, then (on Windows,
where a double-clicked or dragged-onto script's window closes instantly) wait for Enter."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import linecrafty  # noqa: E402


def run(preset: list[str]) -> None:
    try:
        linecrafty.main(preset + sys.argv[1:])
    except SystemExit as e:
        if e.code not in (None, 0):
            print(e.code if isinstance(e.code, str) else f"(exit code {e.code})")
    finally:
        if os.name == "nt":
            print()
            try:
                input("Press Enter to close...")
            except EOFError:
                pass
