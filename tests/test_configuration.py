from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_write_rebuilds_telegram_bridge_model_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path, _ = write_config(
                {
                    "HOME": temporary,
                    "OPENCODE_TELEGRAMTOKEN": "123:secret",
                    "OPENCODE_TELEGRAM_CHAT_ID": "5475045993",
                    "OPENCODE_DEFAULT_PROVIDER": "chatgpt",
                    "OPENCODE_DEFAULT_LLM": "gpt-6-luna",
                    "OPENCODE_API_PORT": "4096",
                }
            )
            self.assertTrue(path.is_file())
            bridge = Path(temporary) / ".config/opencode-telegram-bot/.env"
            content = bridge.read_text()
            self.assertIn("OPENCODE_MODEL_PROVIDER=openai", content)
            self.assertIn("OPENCODE_MODEL_ID=gpt-6-luna", content)
            self.assertIn("OPENCODE_API_URL=http://127.0.0.1:4096", content)
            self.assertNotIn("123:secret", content)
            self.assertNotIn("5475045993", content)
            self.assertEqual(bridge.stat().st_mode & 0o777, 0o600)


class OpenCodeProviderTests(unittest.TestCase):
    def test_openai_v1_groups_discover_models_and_normalize_url(self) -> None:
        class Response:
            def __init__(self, payload: dict[str, object]) -> None:
                self.payload = payload

            def read(self, _limit: int) -> bytes:
                return json.dumps(self.payload).encode()

            def close(self) -> None:
                pass

        def opener(request: object, *, timeout: float) -> Response:
            del timeout
            url = getattr(request, "full_url")
            if "abliteration" in url:
                return Response({"data": [{"id": "abliterated-model"}]})
            if "2001" in url:
                return Response({"data": [{"id": "luna"}, {"id": "sol"}]})
            return Response({"data": []})

        environ = {
            "OPENAI_V1_PROVIDER": "litellm",
            "OPENAI_V1_URL": "https://host.containers.internal",
            "OPENAI_V1_PORT": "2001",
            "OPENAI_V1_KEY": "secret-one",
            "OPENAI_V1_PROVIDER_2": "abliteration",
            "OPENAI_V1_URL_2": "https://api.abliteration.ai/v1",
            "OPENAI_V1_PORT_2": "443",
            "OPENAI_V1_KEY_2": "secret-two",
            "OPENCODE_DEFAULT_PROVIDER": "chatgpt",
            "OPENCODE_DEFAULT_LLM": "gpt-6-luna",
            "OPENCODE_FALLBACK_PROVIDER": "litellm",
            "OPENCODE_FALLBACK_LLM": "luna",
        }
        with patch("opencode_ephemeral.configuration.urlopen", side_effect=opener):
            config = build_config(environ)

        self.assertEqual(config["provider"]["litellm"]["models"], {"luna": {}, "sol": {}})
        self.assertEqual(
            config["provider"]["abliteration"]["options"]["baseURL"],
            "https://api.abliteration.ai:443/v1",
        )
        self.assertEqual(
            config["provider"]["abliteration"]["models"],
            {"abliterated-model": {}},
        )

    def test_explicit_models_survive_discovery_failure(self) -> None:
        with patch(
            "opencode_ephemeral.configuration.urlopen",
            side_effect=TimeoutError,
        ):
            config = build_config(
                {
                    "OPENAI_V1_PROVIDER": "abliteration",
                    "OPENAI_V1_URL": "https://offline.example/v1",
                    "OPENAI_V1_KEY": "secret",
                    "OPENAI_V1_MODELS": "abliterated-model,abliterated-model-large",
                }
            )

        self.assertEqual(
            config["provider"]["abliteration"]["models"],
            {"abliterated-model": {}, "abliterated-model-large": {}},
        )


if __name__ == "__main__":
    unittest.main()
