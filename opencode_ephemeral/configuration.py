"""Build OpenCode's global config from the runtime environment."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .environment import ConfigurationError, clean, without_secret_values
from .mcp import mcp_config


MAX_DISCOVERY_RESPONSE_BYTES = 8 * 1024 * 1024


def _home_path(environ: Mapping[str, str]) -> Path:
    home = Path(clean(environ.get("HOME")) or str(Path.home())).expanduser()
    if not home.is_absolute():
        raise ValueError("HOME must be an absolute path")
    return home


def config_path(environ: Mapping[str, str]) -> Path:
    home = _home_path(environ)
    override = clean(environ.get("OPENCODE_CONFIG"))
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            path = home / path
        return path.resolve()
    return (home / ".config" / "opencode" / "opencode.json").resolve()


def telegram_config_path(environ: Mapping[str, str]) -> Path:
    override = clean(environ.get("OPENCODE_TELEGRAM_HOME"))
    root = Path(override).expanduser() if override else _home_path(environ) / ".config" / "opencode-telegram-bot"
    if not root.is_absolute():
        raise ValueError("OPENCODE_TELEGRAM_HOME must be an absolute path")
    return (root / ".env").resolve()


def _normalize_base_url(raw_url: str, raw_port: str = "") -> str:
    value = clean(raw_url).rstrip("/")
    port = clean(raw_port)
    if not value:
        return ""
    if "://" not in value:
        value = f"http://{value}"
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError("OPENAI_V1_URL must be an HTTP(S) endpoint")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigurationError("OPENAI_V1_URL must not contain credentials")
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise ConfigurationError("OPENAI_V1_URL contains an invalid port") from exc
    if port:
        try:
            requested_port = int(port, 10)
        except ValueError as exc:
            raise ConfigurationError("OPENAI_V1_PORT must be an integer") from exc
        if not 1 <= requested_port <= 65_535:
            raise ConfigurationError("OPENAI_V1_PORT must be between 1 and 65535")
    else:
        requested_port = None

    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    effective_port = parsed_port if parsed_port is not None else requested_port
    netloc = host if effective_port is None else f"{host}:{effective_port}"
    path = parsed.path.rstrip("/") or "/v1"
    return urlunsplit((parsed.scheme, netloc, path, "", ""))


def _configured_models(environ: Mapping[str, str], key_name: str) -> tuple[str, ...]:
    raw = clean(environ.get(key_name))
    if not raw:
        return ()
    try:
        decoded: Any = json.loads(raw)
    except json.JSONDecodeError:
        decoded = [item.strip() for item in raw.replace("\n", ",").split(",")]
    if isinstance(decoded, Mapping):
        decoded = decoded.get("data", decoded.get("models", []))
    if isinstance(decoded, str):
        decoded = [decoded]
    if not isinstance(decoded, list):
        raise ConfigurationError(f"{key_name} must be a JSON array or comma-separated list")

    models: set[str] = set()
    for item in decoded:
        if isinstance(item, str) and clean(item):
            models.add(clean(item))
        elif isinstance(item, Mapping):
            for field in ("id", "model", "name"):
                value = item.get(field)
                if isinstance(value, str) and clean(value):
                    models.add(clean(value))
                    break
    if raw and not models:
        raise ConfigurationError(f"{key_name} must contain at least one model id")
    return tuple(sorted(models))


def _discovered_models(
    base_url: str,
    *,
    key: str,
    opener: Callable[..., Any] | None = None,
    timeout: float = 5.0,
) -> tuple[str, ...]:
    request = Request(
        f"{base_url.rstrip('/')}/models",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {key}",
            "User-Agent": "opencode-ephemeral/1.0",
        },
        method="GET",
    )
    response = (opener or urlopen)(request, timeout=timeout)
    close = getattr(response, "close", None)
    try:
        payload = response.read(MAX_DISCOVERY_RESPONSE_BYTES + 1)
    finally:
        if callable(close):
            close()
    if len(payload) > MAX_DISCOVERY_RESPONSE_BYTES:
        raise ValueError("model discovery response is too large")
    decoded = json.loads(payload.decode("utf-8"))
    rows: Any = decoded.get("data", decoded.get("models", [])) if isinstance(decoded, Mapping) else decoded
    if isinstance(rows, Mapping):
        rows = list(rows.values())
    if not isinstance(rows, list):
        return ()
    models: set[str] = set()
    for row in rows:
        if isinstance(row, str) and clean(row):
            models.add(clean(row))
        elif isinstance(row, Mapping):
            for field in ("id", "model", "name"):
                value = row.get(field)
                if isinstance(value, str) and clean(value):
                    models.add(clean(value))
                    break
    return tuple(sorted(models))


def _provider_groups(
    environ: Mapping[str, str],
    *,
    discover: bool = True,
) -> dict[str, dict[str, Any]]:
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
        base = _normalize_base_url(url, port)
        model_key_name = name("MODELS", index)
        configured_models = _configured_models(environ, model_key_name)
        models = configured_models
        if discover:
            try:
                discovered = _discovered_models(
                    base,
                    key=key,
                    timeout=float(clean(environ.get("OPENAI_V1_DISCOVERY_TIMEOUT")) or 5.0),
                )
            except Exception:
                discovered = ()
            models = tuple(sorted({*configured_models, *discovered}))
        result[provider] = {
            "npm": "@ai-sdk/openai-compatible",
            "name": provider,
            "options": {"baseURL": f"{base}/v1" if not base.endswith("/v1") else base,
                        "apiKey": f"{{env:{key_name}}}"},
            "models": {model: {} for model in models},
        }
    return result


def _configured_routes(
    environ: Mapping[str, str],
    providers: Mapping[str, dict[str, Any]],
) -> tuple[str, ...]:
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
        if provider != "openai" and provider not in providers:
            raise ValueError(f"OpenCode provider {provider!r} is not configured")
        qualified.append(f"{provider}/{model}")
    return tuple(qualified)


def build_config(environ: Mapping[str, str]) -> dict[str, Any]:
    config: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": mcp_config(environ),
    }
    providers = _provider_groups(environ)
    routes = _configured_routes(environ, providers)
    if routes:
        config["model"] = routes[0]
        if len(routes) > 1:
            config["small_model"] = routes[1]
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
        and (name.startswith("MCP_SERVER_BEARER")
             or name in {"OPENCODE_API_KEY", "OPENCODE_SERVER_PASSWORD"})
    }
    if not without_secret_values(config, secrets):
        raise RuntimeError("refusing to persist a resolved secret value")
    return config


def _telegram_model(environ: Mapping[str, str]) -> tuple[str, str] | None:
    provider = clean(environ.get("OPENCODE_MODEL_PROVIDER")) or clean(
        environ.get("OPENCODE_DEFAULT_PROVIDER")
    )
    model = clean(environ.get("OPENCODE_MODEL_ID")) or clean(
        environ.get("OPENCODE_DEFAULT_LLM")
    )
    if not provider or not model:
        return None
    if provider.lower() == "chatgpt":
        provider = "openai"
    return provider, model


def write_telegram_config(environ: Mapping[str, str]) -> Path | None:
    if not clean(environ.get("OPENCODE_TELEGRAMTOKEN")) or not clean(
        environ.get("OPENCODE_TELEGRAM_CHAT_ID")
    ):
        return None
    route = _telegram_model(environ)
    if route is None:
        return None
    provider, model = route
    port = clean(environ.get("OPENCODE_API_PORT")) or "4096"
    content = (
        "# Generated by opencode-ephemeral; secrets stay in the runtime environment.\n"
        "OPENCODE_SERVER_VERSION=v1\n"
        f"OPENCODE_API_URL=http://127.0.0.1:{port}\n"
        f"OPENCODE_MODEL_PROVIDER={provider}\n"
        f"OPENCODE_MODEL_ID={model}\n"
    )
    password = clean(environ.get("OPENCODE_SERVER_PASSWORD"))
    if password and any(password == value for value in (provider, model, port)):
        raise ConfigurationError("refusing to persist a resolved server password")
    destination = telegram_config_path(environ)
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(destination.parent, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent, text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def write_config(environ: Mapping[str, str] | None = None) -> tuple[Path, int]:
    injected = os.environ if environ is None else environ
    destination = config_path(injected)
    config = build_config(injected)
    serialized = json.dumps(config, indent=2, sort_keys=False) + "\n"
    if destination.is_file() and destination.read_text(encoding="utf-8") == serialized:
        os.chmod(destination, 0o600)
        write_telegram_config(injected)
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
    write_telegram_config(injected)
    return destination, len(config["mcp"])
