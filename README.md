# Auto-Continue and Agent Scheduler

Agent Scheduler delivers scheduled messages to Codex and Claude Code sessions. Auto-Continue uses the connector to schedule a one-time continuation in a Codex chat after its next usage reset.

## Install

The connector must be installed and configured before using the Auto-Continue plugin. The installer configures an MCP server and skill for the selected agents and installs a background service. It defaults to local-only operation; a scheduler server URL is optional.

Once the npm package and corresponding binary release are published:

```sh
npx -y @chialuka/agent-scheduler-setup
```

For local development, see [scheduler/README.md](scheduler/README.md). A local source build can be installed with:

```sh
cd scheduler
go build -o agent-scheduler ./cmd/agent-scheduler
./agent-scheduler setup
```

Restart the agent app or start a new session after setup. The root Auto-Continue plugin provides a Codex skill; it uses the separately configured `agent-scheduler` MCP server. See [scheduler/README.md](scheduler/README.md) for supported platforms, optional Cloudflare setup, and current limitations.

## Use

Ask in a Codex chat: “Enable continuation after my next usage reset in this chat.” The connector saves a one-time message for delivery after the reset. Ask to list or cancel scheduled messages when needed. Acceptance of a queued message does not guarantee that the task completed.

Persistent automatic recovery after every usage-limit interruption is not yet implemented. The local device and connector must be available for delivery; sleeping or powered-off devices cannot run the connector.

## Earlier installations

The older Python/macOS scheduler is no longer part of this source tree. Its installed background service and pending timers are independent of Agent Scheduler; they are not migrated or removed automatically. Disable the old service and review any pending timers before enabling the new connector for the same chat.
