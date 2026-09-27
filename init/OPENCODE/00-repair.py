#!/usr/bin/env python3
"""Repair OpenCode's ephemeral provider catalog during container init."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


CONFIG = Path(os.environ.get("OPENCODE_CONFIG", "/root/.config/opencode/opencode.json"))
MAX_RESPONSE = 8 * 1024 * 1024


def value(base: str, index: int) -> tuple[str, str]:
    names = [base] if index == 1 else [f"{base}_{index}", f"{base}_{index:02d}"]
    for name in names:
        current = os.environ.get(name, "").strip()
        if current:
            return current, name
    return "", names[0]


def base_url(raw: str, port: str) -> str:
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlsplit(raw.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("invalid provider URL")
    parsed_port = parsed.port
    requested_port = int(port) if port else None
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    effective_port = parsed_port or requested_port
    netloc = host if effective_port is None else f"{host}:{effective_port}"
    path = parsed.path.rstrip("/") or "/v1"
    return urlunsplit((parsed.scheme, netloc, path, "", ""))


def model_ids(raw: str) -> set[str]:
    if not raw:
        return set()
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        decoded = [part.strip() for part in raw.replace("\n", ",").split(",")]
    if isinstance(decoded, dict):
        decoded = decoded.get("data", decoded.get("models", []))
    if isinstance(decoded, str):
        decoded = [decoded]
    if not isinstance(decoded, list):
        return set()
    result: set[str] = set()
    for row in decoded:
        if isinstance(row, str) and row.strip():
            result.add(row.strip())
        elif isinstance(row, dict):
            for field in ("id", "model", "name"):
                item = row.get(field)
                if isinstance(item, str) and item.strip():
                    result.add(item.strip())
                    break
    return result


def discover(url: str, key: str) -> set[str]:
    request = Request(
        f"{url.rstrip('/')}/models",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {key}",
            "User-Agent": "opencode-init-repair/1.0",
        },
    )
    with urlopen(request, timeout=5) as response:
        payload = response.read(MAX_RESPONSE + 1)
    if len(payload) > MAX_RESPONSE:
        raise ValueError("provider catalog is too large")
    return model_ids(payload.decode("utf-8"))


def wait_for_config_service() -> None:
    try:
        subprocess.run(
            ["systemctl", "start", "opencode-config.service"],
            check=False,
            timeout=450,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"opencode config service not ready: {exc}")


def repair() -> bool:
    wait_for_config_service()
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    try:
        config = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read {CONFIG}: {exc}")
        return False
    if not isinstance(config, dict):
        print(f"cannot repair non-object config: {CONFIG}")
        return False

    providers = config.setdefault("provider", {})
    if not isinstance(providers, dict):
        providers = {}
        config["provider"] = providers
    changed = False
    for index in range(1, 100):
        provider, provider_name = value("OPENAI_V1_PROVIDER", index)
        url, _ = value("OPENAI_V1_URL", index)
        key, key_name = value("OPENAI_V1_KEY", index)
        if not provider and not url and not key:
            continue
        if not provider or not url or not key:
            print(f"skipping incomplete OpenAI V1 group {index}")
            continue
        try:
            endpoint = base_url(url, value("OPENAI_V1_PORT", index)[0])
        except (ValueError, TypeError) as exc:
            print(f"skipping {provider}: {exc}")
            continue

        entry = providers.setdefault(
            provider,
            {
                "npm": "@ai-sdk/openai-compatible",
                "name": provider,
                "options": {},
                "models": {},
            },
        )
        if not isinstance(entry, dict):
            entry = {
                "npm": "@ai-sdk/openai-compatible",
                "name": provider,
                "options": {},
                "models": {},
            }
            providers[provider] = entry
            changed = True
        options = entry.setdefault("options", {})
        if not isinstance(options, dict):
            options = {}
            entry["options"] = options
            changed = True
        if options.get("baseURL") != endpoint:
            options["baseURL"] = endpoint
            changed = True
        api_key = f"{{env:{key_name}}}"
        if options.get("apiKey") != api_key:
            options["apiKey"] = api_key
            changed = True
        models = entry.setdefault("models", {})
        if not isinstance(models, dict):
            models = {}
            entry["models"] = models
            changed = True
        discovered = set()
        try:
            discovered = discover(endpoint, key)
        except Exception as exc:
            print(f"model discovery unavailable for {provider}: {exc}")
        explicit, _ = value("OPENAI_V1_MODELS", index)
        for model in discovered | model_ids(explicit):
            if model not in models:
                models[model] = {}
                changed = True
        print(f"{provider}: {len(models)} models ({provider_name})")

    if changed:
        fd, temporary = tempfile.mkstemp(prefix=".opencode.", suffix=".json", dir=CONFIG.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(config, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, CONFIG)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        print(f"repaired {CONFIG}")
    else:
        print(f"OpenCode config already contains the discovered providers: {CONFIG}")
    return True


if __name__ == "__main__":
    if repair():
        result = subprocess.run(["systemctl", "restart", "opencode.service"], check=False)
        if result.returncode:
            print(f"opencode.service restart returned {result.returncode}")
