"""Translate the shared MCP_SERVER_* groups into OpenCode MCP config."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .environment import ConfigurationError, clean


MAX_MCP_SERVERS = 50
MCP_FIELDS = ("NAME", "URL", "BEARER", "ALLOW_PRIVATE", "ALLOW")
MCP_SUFFIX = re.compile(
    r"^(?:MCP_SERVER_(?:NAME|URL|BEARER|ALLOW_PRIVATE)|MCP_ALLOW)_(\d+)$"
)
SAFE_SERVER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class McpServer:
    index: int
    name: str
    url: str
    bearer_env: str | None

    def opencode_config(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": "remote",
            "url": self.url,
            "enabled": True,
            "oauth": False,
        }
        if self.bearer_env is not None:
            result["headers"] = {
                "Authorization": f"Bearer {{env:{self.bearer_env}}}"
            }
        return result


def _field_name(environ: Mapping[str, str], field: str, index: int) -> str:
    base = "MCP_ALLOW" if field == "ALLOW" else f"MCP_SERVER_{field}"
    if index == 1:
        return base
    padded = f"{base}_{index:02d}"
    unpadded = f"{base}_{index}"
    for candidate in (padded, unpadded):
        if candidate in environ:
            return candidate
    return padded


def _indexes(environ: Mapping[str, str]) -> tuple[int, ...]:
    indexes = {1}
    for key in environ:
        match = MCP_SUFFIX.fullmatch(key)
        if match is None:
            continue
        suffix = match.group(1)
        index = int(suffix, 10)
        if not 2 <= index <= MAX_MCP_SERVERS:
            raise ConfigurationError(
                f"{key} index must be between 02 and {MAX_MCP_SERVERS:02d}"
            )
        if suffix not in {str(index), f"{index:02d}"}:
            raise ConfigurationError(f"{key} has an unsupported numeric suffix")
        indexes.add(index)
    return tuple(sorted(indexes))


def _url(raw_url: str, index: int) -> str:
    value = clean(raw_url)
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError(
            f"MCP_SERVER_URL for server {index:02d} must be an http(s) URL"
        )
    if parsed.username is not None or parsed.password is not None:
        raise ConfigurationError(
            f"MCP_SERVER_URL for server {index:02d} must not contain credentials"
        )
    return value


def _name(raw_name: str, url: str, index: int) -> str:
    value = clean(raw_name)
    if not value:
        value = clean(urlsplit(url).hostname)
        if value.endswith(".dns.podman"):
            value = value[: -len(".dns.podman")]
        value = value or f"mcp-{index:02d}"
    if SAFE_SERVER_NAME.fullmatch(value) is None:
        raise ConfigurationError(
            f"MCP server {index:02d} name must match {SAFE_SERVER_NAME.pattern}"
        )
    return value


def discover_mcp_servers(environ: Mapping[str, str]) -> tuple[McpServer, ...]:
    """Read the same suffixless, then suffix02-style groups as the other runtimes."""

    servers: list[McpServer] = []
    seen_names: set[str] = set()
    for index in _indexes(environ):
        fields = {field: _field_name(environ, field, index) for field in MCP_FIELDS}
        raw_url = clean(environ.get(fields["URL"]))
        if not raw_url:
            continue
        url = _url(raw_url, index)
        name = _name(environ.get(fields["NAME"], ""), url, index)
        folded_name = name.casefold()
        if folded_name in seen_names:
            raise ConfigurationError(f"duplicate MCP server name: {name}")
        seen_names.add(folded_name)

        bearer = clean(environ.get(fields["BEARER"]))
        if bearer.lower().startswith("bearer "):
            raise ConfigurationError(
                f"{fields['BEARER']} must contain only the token, without 'Bearer '")
        servers.append(
            McpServer(
                index=index,
                name=name,
                url=url,
                bearer_env=fields["BEARER"] if bearer else None,
            )
        )
    return tuple(servers)


def mcp_config(environ: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    return {server.name: server.opencode_config() for server in discover_mcp_servers(environ)}
