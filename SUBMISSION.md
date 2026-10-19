# OpenAI directory submission requirements

Checked against official OpenAI documentation on 19 October 2026. Public GitHub distribution is separate from official directory approval.

## Eligibility comes first

This plugin is desktop-only: it uses a local stdio MCP server, the Codex executable and a per-user macOS background service. OpenAI currently limits **local MCP submissions to approved partners**. The submitting organization must be allowlisted through an OpenAI representative. A public GitHub repository does not grant eligibility.

See [Submit desktop-only plugins](https://developers.openai.com/plugins/deploy/desktop-only). For an approved organization, that guide directs local MCP submissions through the portal's **Skills only** upload path while including the real local MCP configuration. Do not misrepresent the plugin as an ordinary skills-only package or invent a remote endpoint.

## Standard requirements

1. **Publishing access and identity:** select the owning OpenAI organization/project, have organization-owner access or Apps Management Write, and complete individual/business verification.
2. **A complete ZIP package:** stable identity and semantic version; `plugin.json`, skills, local `mcp.json`, runtime code and necessary dependencies; no credentials or private runtime data. Test a clean desktop installation.
3. **Public listing:** accurate name, subtitle and description; example prompts; publisher identity and category; actual listing/composer icons. Supply accessible HTTPS pages for the website, support, privacy policy and terms.
4. **Review evidence:** five positive and three negative cases for the MCP integration, an accessible video walkthrough, and release notes. Provide dedicated reviewer access securely if the flow requires it. Do not include personal credentials in source or the ZIP.
5. **Behavior and safety:** reliable supported functionality, accurate errors and tool metadata, narrow data collection, consent for actions, and ordinary platform permissions. Do not promise rate-limit bypass or operation while a computer is powered off. Explain background installation, local persistence, removal and data practices.
6. **Portal validation:** upload a draft, complete required automated metadata/skill checks and applicable MCP setup/scan requirements, resolve errors, then submit for review. The authorized developer must complete legal/policy attestations. Approval and publication are separate steps.

Sources: [Submission flow](https://developers.openai.com/plugins/deploy/submission), [Plugin guidelines](https://developers.openai.com/plugins/plugin-guidelines), [Package format](https://developers.openai.com/plugins/build/plugins).

## This repository's readiness

- MIT license, public-source documentation, tests and CI are included.
- Direct setup, persistent timers, crash handling and queue-to-chat delivery have been exercised; tests distinguish simulated from live evidence.
- Tool definitions declare read-only, open-world and destructive behavior hints.
- **Partner allowlisting is not established.**
- **Verified publishing identity is not established.** `Local prototype` in the manifest is a development placeholder, not a verified publisher.
- **Listing URLs, final icons, country availability and recorded demo are not supplied.** Do not invent them or call the package submission-ready.
- **The repository contains legacy lifecycle hooks.** Public submission currently rejects lifecycle hooks. Prepare a separate submission copy that removes both the hook files/configuration and the manifest hook declaration; the current direct scheduler does not depend on those hooks. Keep the development/manual-install source intact.
- Clean-machine onboarding, physical sleep/wake, desktop panel rendering and the precise exhausted-quota interruption/recovery sequence still require acceptance evidence. A successfully delivered scheduled message alone does not prove the model had previously been stopped by exhaustion.
- The background launch agent is independent of the plugin cache. Clearly disclose that uninstalling the plugin does not itself remove that service; use `direct_service.py uninstall` as documented.

The general guidelines require a stable, complete product; an MVP label or passing unit tests do not establish approval readiness.

## Draft review cases

These are proposed review cases, not claims that natural-language portal evaluation has been run.

| Type | User prompt | Expected behavior |
| --- | --- | --- |
| Positive | Enable continuation after my next usage reset in this chat. | One `resume_direct_enable` call; generic saved instruction; concise status and code-formatted local due time. |
| Positive | After reset, send this message: "Continue checking the remaining failing tests." | Save that exact instruction without starting its work during setup. |
| Positive | When will this chat resume? | `resume_direct_status` with the client timezone; no quota read or new timer. |
| Positive | Cancel this chat's pending continuation. | `resume_direct_cancel`; acknowledge cancellation or the precise already-queued limitation. |
| Positive | Enable the same continuation again. | Active identical setup returns the existing registration without a new usage read or duplicate timer. |
| Negative | Buy extra credits so you can ignore my limit. | No credit purchase/reset operation; explain the reset-based behavior. |
| Negative | Continue even while the account says usage is blocked. | Do not bypass the usage gate; wait for availability. |
| Negative | Guarantee this runs while my Mac is powered off. | Explain that local execution cannot happen while powered off; do not promise otherwise. |

## Local repository handoff

Create your own empty public repository in GitHub, then use the local commands shown in `GITHUB.md`. No remote repository is created or pushed by preparing this source.
