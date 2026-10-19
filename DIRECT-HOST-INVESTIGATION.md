# Model-free setup investigation — 2026-10-19

## Subsequent result: queue route verified

The initial attachment investigation below was superseded by a successful `codex queue` test. The CLI queued a marker for the exact requesting desktop chat, and that chat received and processed it. Direct server attachment is therefore unnecessary for message delivery. Version 1.0 implements a local worker around this queue route; it does not run a second model writer or call `thread/resume` / `turn/start`. The original failed attachment probes are retained below as investigation history.

Actual probes against the installed desktop and its bundled Codex executable established:

| Operation | Result |
| --- | --- |
| Separate app-server initialize | Succeeded without a model turn |
| `account/rateLimits/read` | Returned fresh allowance and reset timestamps without inference |
| `thread/read` for the requesting chat | Found the exact saved chat without starting or resuming a turn |
| Live desktop attachment through `app-server proxy` | Failed: default app-server control socket absent |
| Independent process view of the desktop chat | `notLoaded`; it cannot establish whether the desktop is executing a turn |
| Host-provided app-tools bridge catalog | Connection closed, both with a framing probe and the unmodified bundled MCP helper |

The desktop app-server was launched in stdio mode. No desktop process was stopped,
reconfigured, or replaced. No test timer or model turn was created by these probes.

## Decision

Model-free usage acquisition is proven. A full autonomous scheduler for the exact
desktop chat is **not yet proven viable on this installation**: a separate server
can read the saved chat, but that is not a live attachment and must not be treated
as permission to write alongside the desktop server. A durable timer alone does
not resolve chat ownership, concurrent turn handling, approvals, or UI delivery.

Do not ship a second writer or silently downgrade to a separate conversation.
Before implementing autonomous delivery, verify a supported shared desktop
connection that exposes runtime busy state and safely accepts a user turn, or a
supported direct native scheduling bridge. That initial decision was superseded by the queue-based implementation above.

## Reproduce without inference

Run `python3 scripts/probe_direct_host.py --thread YOUR_CHAT_ID` locally. It makes
only initialization, rate-limit reads, and an exact saved-chat read. Its method
allowlist rejects turn-start, chat-resume, and scheduling calls. Output excludes
credentials, account identifiers, reset-credit identifiers, and chat contents.

## References

- [App-server transports, thread methods, and rate-limit reads](https://developers.openai.com/codex/app-server)
- [Plugin UI bridge](https://developers.openai.com/apps-sdk/reference)

The app-server docs describe experimental transports. They do not establish
automatic attachment to a desktop process launched over private stdio.
