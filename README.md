# opencode-ephemeral

Small runtime configurator for the OpenCode service shipped in the Fedora Core
image. At container start the oneshot unit rebuilds
`~/.config/opencode/opencode.json` from the shared `MCP_SERVER_*` environment
groups, just like the Hermes and OpenClaw ephemeral layers. It also rebuilds the
non-secret model/API settings consumed by the equal-level OpenCode Telegram
bridge, so a missing volume is recoverable from the injected runtime
environment.

Each configured `OPENAI_V1_*` group is queried through its `/v1/models` endpoint
and the returned IDs are added to that provider's OpenCode model map. An
optional `OPENAI_V1_MODELS` (and numbered suffixes) value accepts a comma-
separated list or JSON array and is merged with discovery results, or remains
as the fallback when discovery is unavailable. `OPENAI_V1_DISCOVERY_TIMEOUT`
controls the per-provider request timeout in seconds and defaults to `5`.
URLs with a separate `OPENAI_V1_PORT` are normalized before their path, so an
endpoint such as `https://api.example/v1` with port `443` becomes a valid
`https://api.example:443/v1` base URL.

Only URLs, names, and environment placeholders are written. Bearer values are
never persisted. `MCP_SERVER_ALLOW_PRIVATE` and `MCP_ALLOW` remain in the
shared example for compatibility; their policy meanings belong to OpenClaw,
while OpenCode consumes every configured HTTP MCP group.
