"""Command line entry point for opencode-ephemeral."""

from __future__ import annotations

import argparse
import sys

from .configuration import write_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("configure",))
    args = parser.parse_args(argv)
    if args.command == "configure":
        path, count = write_config()
        print(f"OpenCode MCP servers configured: {count} ({path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
