#!/usr/bin/env python3
"""Installed launcher for the opencode-ephemeral package."""

from __future__ import annotations

import sys
from pathlib import Path


package_root = Path(__file__).resolve().parent.parent / "lib" / "opencode-ephemeral"
sys.path.insert(0, str(package_root))

from opencode_ephemeral.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
