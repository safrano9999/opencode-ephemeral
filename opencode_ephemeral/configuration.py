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


def _provider_groups(environ: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    def name(field: str, index: int) -> str:
        if index == 1:
            return f"OPENAI_V1_{field}"
        for name in (f"OPENAI_V1_{field}_{index:02d}", f"OPENAI_V1_{field}_{index}"):
            if name in environ:
                return name
        return f"OPENAI_V1_{field}_{index:02d}"

    def value(field: str, index: int) -> str:
        return clean(environ.get(name(field, index)))

    result: dict[str, dict[str, Any]] = {}
    for index in range(1, 51):
        suffix = "" if index == 1 else f"_{index:02d}"
        provider = value("PROVIDER", index)
        url = value("URL", index)
        key_name = name("KEY", index)
        key = clean(environ.get(key_name))
        if not provider and not url:
            continue
        if not provider or not url or not key:
            raise ValueError(f"OPENAI_V1 group {index:02d} is incomplete")
        port = value("PORT", index)
        base = url.rstrip("/")
        if "://" not in base:
            base = f"https://{base}"
        if port and "://" in base and base.rsplit(":", 1)[-1].isdigit() is False:
            base = f"{base}:{port}"
        raw_models = value("MODELS", index)
        models = [item.strip() for item in raw_models.replace("\n", ",").split(",") if item.strip()]
        result[provider] = {
            "npm": "@ai-sdk/openai-compatible",
            "name": provider,
            "options": {"baseURL": f"{base}/v1" if not base.endswith("/v1") else base,
                        "apiKey": f"{{env:{key_name}}}"},
            "models": {model: {} for model in models},
        }
    return result


def _configured_routes(environ: Mapping[str, str]) -> tuple[str, ...]:
    provider = clean(environ.get("OPENCODE_DEFAULT_PROVIDER"))
    model = clean(environ.get("OPENCODE_DEFAULT_LLM"))
    if bool(provider) != bool(model):
        raise ValueError("OPENCODE_DEFAULT_PROVIDER and OPENCODE_DEFAULT_LLM must be set together")
    if not provider:
        return ()
    routes = [(provider, model)]
    for index in range(1, 51):
        suffix = "" if index == 1 else f"_{index:02d}"
        p = clean(environ.get(f"OPENCODE_FALLBACK_PROVIDER{suffix}"))
        m = clean(environ.get(f"OPENCODE_FALLBACK_LLM{suffix}"))
        if not p and not m:
            continue
        if bool(p) != bool(m):
            raise ValueError(f"OpenCode fallback {index:02d} must be a complete pair")
        routes.append((p, m))
    qualified: list[str] = []
    for provider, model in routes:
        provider = "openai" if provider.lower() == "chatgpt" else provider.lower()
        configured = _provider_groups(environ)
        if provider != "openai" and provider not in configured:
            raise ValueError(f"OpenCode provider {provider!r} is not configured")
        qualified.append(f"{provider}/{model}")
    return tuple(qualified)


def build_config(environ: Mapping[str, str]) -> dict[str, Any]:
    config: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": mcp_config(environ),
    }
    routes = _configured_routes(environ)
    if routes:
        config["model"] = routes[0]
        if len(routes) > 1:
            config["small_model"] = routes[1]
        providers = _provider_groups(environ)
        for route in routes:
            provider, model = route.split("/", 1)
            if provider in providers:
                providers[provider]["models"].setdefault(model, {})
        if providers:
            config["provider"] = providers
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
