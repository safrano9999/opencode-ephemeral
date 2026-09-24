"""Small, strict helpers for the injected container environment."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class ConfigurationError(ValueError):
    """Raised when the runtime environment cannot produce valid config."""


def clean(value: str | None) -> str:
    return (value or "").strip()


def without_secret_values(value: Any, secrets: set[str]) -> bool:
    if isinstance(value, Mapping):
        return all(without_secret_values(item, secrets) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(without_secret_values(item, secrets) for item in value)
    return not (isinstance(value, str) and value in secrets)
