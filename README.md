# Auto-Continue

A plugin that schedules the saved continuation instruction for delivery into the same Codex chat one minute after the next usage reset. The cross-platform `agent-scheduler` connector handles timing and delivery.

## Use it

After installing, invoke the plugin in the chat you want to resume and say:

> Enable continuation after my next usage reset in this chat.

The agent makes one `schedule_auto_continue` call. The connector reads the reset timestamp locally, adds one minute, and saves the generic interrupted-task instruction. After success, the agent stops without a separate confirmation message. It does not inspect or implement the task during setup. If you explicitly provide a custom instruction, that message is saved and delivered later.

Opening a usage panel is not required. Its controls remain an optional convenience. Chat invocation starts a model turn; skill instructions can minimize the work, but cannot force zero hidden reasoning or select a separate setup model. Waiting, quota checks and queue submission use local code without inference.

The saved instruction stays unchanged after enabling. Later messages and apparent task completion do not cancel it; explicitly ask to cancel if you want to stop delivery. Automatic enrollment of every chat is not part of this release.

## Install and verify

After the owner publishes this repository, other users can add its repo marketplace (replace the placeholders with the actual public GitHub repository):

```sh
codex plugin marketplace add YOUR_ACCOUNT/YOUR_REPOSITORY
codex plugin add auto-continue@auto-continue-community
```

This is manual repository distribution, separate from OpenAI directory approval.

The source stays in this folder. Register it using `python3 scripts/install_local.py`, then install/update through the host plugin manager or `codex plugin add auto-continue@local-codex-plugins`. Refresh the desktop plugin view if it still shows an older version. Do not edit version caches.

The connector installer is being built as one interactive `npx` flow. It will
ask which agents to configure, then install the skill and update their MCP
settings while setting up the background connector. The installer source and
release packaging are in progress under [`scheduler-v2/`](scheduler-v2/).

Until its npm package and first binary release are published, local developers
can build the connector from source with Go 1.26 or newer:

```sh
cd scheduler-v2
go build -o agent-scheduler ./cmd/agent-scheduler
./agent-scheduler setup --url https://scheduler.example.com
```

Setup enrolls the device, installs the background connector, and configures the scheduler MCP server in Codex. The message and real chat reference stay on the device; the server receives opaque delivery metadata. The Codex CLI must support app-server usage reads and `queue`. Connector implementation and Cloudflare deployment instructions are in [`scheduler-v2/README.md`](scheduler-v2/README.md).

## Contributing

Install the pinned Python formatter and check the Python source:

```sh
python3 -m pip install -r requirements-dev.txt
ruff format hooks scripts
ruff format --check hooks scripts
```

Ruff formats the Python server and worker code. JSON, Markdown, and HTML files are maintained in the repository's existing style.

## Scheduler behavior

Auto-Continue uses the same local message store and delivery worker as scheduled
messages. The connector reads Codex usage when scheduling, saves the message for
one minute after the applicable reset, and queues it when due. The setup turn
only schedules; the task begins in the later agent turn. Message content and
real chat references remain on the device. The server stores opaque job
metadata and sends a wake signal.

The connector provides `list_scheduled_messages` and
`cancel_scheduled_message` for inspection and cancellation. An uncertain
delivery is never replayed automatically. Device retries are scheduled at
`+1, +3, +6, +10, +15 minutes`; an offline connector receives the job when it
reconnects, while the job remains valid.

## Legacy prototype

The older Python/macOS implementation remains in `scripts/` for compatibility,
but the Auto-Continue skill now schedules through the Go `agent-scheduler`
connector. Do not enable both implementations for the same chat.

## Public source and directory submission

Licensed under [MIT](LICENSE). This is an independent project and does not claim OpenAI endorsement. Manual GitHub marketplace distribution is separate from OpenAI directory review. No remote repository is configured by this source package.
