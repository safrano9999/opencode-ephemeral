from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from opencode_ephemeral.configuration import build_config, write_config
from opencode_ephemeral.environment import ConfigurationError
from opencode_ephemeral.mcp import discover_mcp_servers


class OpenCodeMcpTests(unittest.TestCase):
    def test_shared_groups_become_remote_servers_without_secrets(self) -> None:
        environ = {
            "MCP_SERVER_NAME": "general",
            "MCP_SERVER_URL": "http://general.example.test/mcp",
            "MCP_SERVER_BEARER": "general-secret",
            "MCP_SERVER_ALLOW_PRIVATE": "1",
            "MCP_ALLOW": "main",
            "MCP_SERVER_NAME_02": "mtg",
            "MCP_SERVER_URL_02": "https://mtg.example.test/mcp",
            "MCP_SERVER_BEARER_02": "mtg-secret",
            "MCP_SERVER_ALLOW_PRIVATE_02": "0",
            "MCP_ALLOW_02": "mtg",
        }
        config = build_config(environ)
        self.assertEqual(sorted(config["mcp"]), ["general", "mtg"])
        self.assertEqual(
            config["mcp"]["mtg"]["headers"]["Authorization"],
            "Bearer {env:MCP_SERVER_BEARER_02}",
        )
        serialized = json.dumps(config)
        self.assertNotIn("general-secret", serialized)
        self.assertNotIn("mtg-secret", serialized)

    def test_empty_bearer_omits_headers(self) -> None:
        config = build_config(
            {
                "MCP_SERVER_NAME": "public",
                "MCP_SERVER_URL": "https://public.example.test/mcp",
            }
        )
        self.assertNotIn("headers", config["mcp"]["public"])

    def test_bearer_prefix_is_rejected(self) -> None:
        with self.assertRaises(ConfigurationError):
            discover_mcp_servers(
                {
                    "MCP_SERVER_URL": "https://example.test/mcp",
                    "MCP_SERVER_BEARER": "Bearer already-prefixed",
                }
            )

    def test_write_uses_global_opencode_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path, count = write_config(
                {
                    "HOME": temporary,
                    "MCP_SERVER_URL": "https://example.test/mcp",
                }
            )
            self.assertEqual(count, 1)
            self.assertEqual(
                path, Path(temporary) / ".config/opencode/opencode.json"
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
