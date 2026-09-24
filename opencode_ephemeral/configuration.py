"""Build OpenCode's global config from the runtime environment."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .environment import clean, without_secret_values
from .mcp import mcp_config


def config_path(environ: Mapping[str, str]) -> Path:
    home = Path(clean(environ.get("HOME")) or str(Path.home())).expanduser()
    if not home.is_absolute():
        raise ValueError("HOME must be an absolute path")
    override = clean(environ.get("OPENCODE_CONFIG"))
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            path = home / path
        return path.resolve()
    return (home / ".config" / "opencode" / "opencode.json").resolve()


def build_config(environ: Mapping[str, str]) -> dict[str, Any]:
    config: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": mcp_config(environ),
    }
    secrets = {
        clean(value)
        for name, value in environ.items()
        if clean(value)
        and (name.startswith("MCP_SERVER_BEARER") or name == "OPENCODE_API_KEY")
    }
    if not without_secret_values(config, secrets):
        raise RuntimeError("refusing to persist a resolved secret value")
    return config


def write_config(environ: Mapping[str, str] | None = None) -> tuple[Path, int]:
    injected = os.environ if environ is None else environ
    destination = config_path(injected)
    config = build_config(injected)
    serialized = json.dumps(config, indent=2, sort_keys=False) + "\n"
    if destination.is_file() and destination.read_text(encoding="utf-8") == serialized:
        os.chmod(destination, 0o600)
        return destination, len(config["mcp"])

    destination.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(destination.parent, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent, text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination, len(config["mcp"])
