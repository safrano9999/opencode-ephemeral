# opencode-ephemeral

Small runtime configurator for the OpenCode service shipped in the Fedora Core
image. At container start the oneshot unit rebuilds
`~/.config/opencode/opencode.json` from the shared `MCP_SERVER_*` environment
groups, just like the Hermes and OpenClaw ephemeral layers. It also rebuilds the
non-secret model/API settings consumed by the equal-level OpenCode Telegram
bridge, so a missing volume is recoverable from the injected runtime
environment.

Only URLs, names, and environment placeholders are written. Bearer values are
never persisted. `MCP_SERVER_ALLOW_PRIVATE` and `MCP_ALLOW` remain in the
shared example for compatibility; their policy meanings belong to OpenClaw,
while OpenCode consumes every configured HTTP MCP group.
