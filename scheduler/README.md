# Agent Scheduler

Cross-platform scheduler for agent sessions, with Auto-Continue as a job type.


## Data boundary

- The local connector stores message text, provider, and the real session
  reference.
- The server stores pseudonymous user and device records plus opaque job IDs,
  due times, expiry, and delivery state.
- The server does not receive message content, provider credentials, or real
  agent session IDs.
- The server sends a `job_due` wake signal. The local connector validates the
  corresponding local record and due time before calling a host adapter.

The Cloudflare implementation lives in `cloudflare/`: a TypeScript Worker
serves the HTTPS API, a Durable Object holds each device WebSocket, a Workflow
waits until due and retries delivery, and D1 stores only user/device/job
metadata. The Go process remains on the user's device.

## Retry policy

Delivery signal retries are due at +1, +3, +6, +10, and +15 minutes after the
scheduled time. A disconnected device has no live socket to receive a signal;
its job stays pending and is sent when it reconnects, until the job expires.

## Current implementation

`internal/jobs` defines distinct local and server job types and validates the
retry schedule. `migrations/server/001_init.sql` defines the metadata-only
server schema. Message, provider, work directory, and real session reference
are deliberately absent from the server schema.

## Install for selected agents

The single interactive installer lets a user select Codex, Claude Code, or
both. It downloads and verifies the matching connector release, installs the
Agent Scheduler skill for each selected agent, updates that agent's MCP
configuration, and installs the resident connector service. Cloud enrollment
is optional.

Once the npm package and first GitHub release are published, the user flow is:

```sh
npx -y @chialuka/agent-scheduler-setup
```

The installer defaults to local-only operation. Enter a deployed Worker URL
only if cloud delivery signals are desired. For local development, build the
connector and run the same setup path:

```sh
go build -o agent-scheduler ./cmd/agent-scheduler
./agent-scheduler setup
```

The connector installs the executable to the user's local bin directory and
installs a per-user startup service. When a Worker URL is supplied, setup also
enrolls one pseudonymous user/device and saves the bearer token locally. Existing
cloud configuration is preserved if setup is rerun without a URL. The npx installer configures only the
agents the user selected. Codex Desktop is configured directly through its
user `config.toml`; Claude Code is configured in its user-level
`~/.claude.json` MCP settings. When cloud delivery is configured, the resident connector maintains a WebSocket. It delivers due messages while agent apps are closed, as long as the device and service are running. For local-only use, run the executable as
an MCP server without `setup`. Executables can be overridden with
`AGENT_SCHEDULER_CODEX` and `AGENT_SCHEDULER_CLAUDE`; `AGENT_SCHEDULER_DB`
overrides the database path.

The generic `schedule_message` call needs the provider's local session
reference, working directory, message, due time, and expiry. For Codex
Auto-Continue, `schedule_auto_continue` instead reads account limits through
the local Codex app-server and saves the standard or explicitly requested
message for one minute after the applicable reset. It does not ask the model to
calculate the reset. Both calls save locally and return without waiting for a
cloud request; the background connector syncs opaque metadata. The host must
not begin the requested work in that turn. At the due time, the connector
delivers the saved message to the target agent session. Codex delivery uses its
local `codex queue` command; Claude Code delivery starts a resumed CLI turn.
The worker marks failures `uncertain` rather than replaying a message that
could already have been accepted.

## Cloudflare deployment

Install dependencies in `cloudflare/`, create the D1 database, replace the
placeholder `database_id` in `wrangler.jsonc`, then apply migrations and deploy.
The enrollment rate-limit binding uses namespace ID `1001`; choose an unused
integer for your Cloudflare account if that namespace is already in use.

```sh
npm install
npx wrangler d1 create agent-scheduler
npx wrangler d1 migrations apply agent-scheduler --remote
npm run deploy
```

The Worker currently implements enrollment with a per-IP edge rate limit,
opaque job create/list/cancel, delivery acknowledgements, authenticated device
WebSockets, and durable due signals with attempts at due, +1, +3, +6, +10, and
+15 minutes. Cloudflare's rate-limit counters are approximate and local to each
Cloudflare location, so the enrollment binding is an abuse guard rather than a
strict global quota. D1 is adequate for this initial metadata-only service; its
per-database throughput and storage limits should be reviewed as usage grows. The PostgreSQL migration
under `migrations/server/` is a reference schema; Cloudflare uses the D1
migration under `cloudflare/migrations/`.

## Remaining work

The connector setup and per-user service definitions are implemented for
macOS, Linux, and Windows, but have not been exercised on all three systems. The
npm package and GitHub release assets must be published before the one-command
`npx` flow is available to end users. Provider
session-context discovery is still missing: the scheduling tools need the
current local session reference and working directory from the host. The
cross-platform release packaging/update flow is also not complete. Auto-Continue
requires the local Codex CLI's app-server usage read and queue support.
End-to-end verification against a deployed Cloudflare account and real
Codex/Claude sessions remains outstanding.

## Persistent per-chat auto-continue (experimental)

`set_auto_continue` accepts `sessionRef`, `workDir`, and `enabled`. Enable once
per chat; inspect with `get_auto_continue`. Activation first verifies stored
turn access, and uses the current turn as a baseline so old failures are not
replayed. Disabling cancels pending watcher jobs, but cannot retract a delivery
already claimed by a worker.

The Auto-Continue plugin bundles a `PostModelRequest` MCP hook calling
`agent-scheduler.record_model_request_outcome`. Review and trust this hook in
Codex Settings > Hooks (or `/hooks` in the CLI), and keep the connector MCP
connection available. An installer-only skill installation does not install
this bundled hook; install the root plugin as well for persistent mode.

The hook returns immediately after saving a failed-request hint locally.
Completed/cancelled requests, disabled chats, and identified subagent callbacks
are ignored. A unique chat/turn key deduplicates hints. The existing job worker
checks pending hints after two seconds through `thread/turns/list`. If the turn
is still in progress or inspection fails, it retries at ten-second intervals,
up to six checks total. A latest turn with status `failed` and error
`usageLimitExceeded` creates one reset job. A successful retry or newer turn
causes the hint to be discarded. Exhausted verification is logged and discarded;
there is no periodic scan of enabled chat history. A late duplicate callback
can trigger a fresh verification, but cannot duplicate the continuation job. A local unique constraint on chat
and failed turn deduplicates observations across processes and restarts. The
preference remains enabled after delivery. Immediately before claiming a job,
the worker checks that this same failure is still the latest turn. A later
turn supersedes it. Temporary inspection failures defer delivery until expiry.
Only the latest stored turn is considered. Hints survive connector restarts,
but hooks missed while the MCP connection is unavailable cannot be recovered
without an explicit inspection. Work superseded while offline is not resumed.

Delivery now uses `codex queue --resume`. Successful command exit means queued,
not confirmed execution: this CLI can warn that resume failed while still
exiting successfully. The scheduler's existing `delivered` state therefore
means queue acceptance only. Ambiguous CLI failures remain `uncertain` and are
not automatically replayed. The watcher does not confirm a resumed turn has
started. Device sleep, unsupported history APIs, missing CLI, and disconnected
hosts can prevent continuation. Real desktop cross-process history visibility
and a real usage-limit/resume cycle still require end-to-end verification.
