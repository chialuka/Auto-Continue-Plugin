# Direct scheduler installation (1.0)

The default setup is now the direct local scheduler described in [README.md](README.md). Refresh/reinstall the plugin, open its meter in the desired chat, and click **Enable for this chat**. One user action lets code install/start the per-user macOS service, read usage, and save the timer without a model turn. A chat request uses one `resume_direct_enable` call instead of the native scheduling flow below.

Confirm `resume_direct_enable`, `resume_direct_status`, and `resume_direct_cancel` are discoverable. The local service can also be installed with `python3 scripts/direct_service.py install`. Existing native registrations must be explicitly disabled before moving a chat to direct scheduling. To uninstall fully, run `python3 scripts/direct_service.py uninstall` in addition to removing the plugin.

The following instructions document legacy native registrations and marketplace registration. They are not the default 1.0 enablement path.

# Local installation in Codex desktop

This is local installation, not a public-directory upload. It works only in a desktop host that supports local plugins and exposes the required usage and automation tools. The package deliberately checks those capabilities instead of assuming them.

## 1. Check Python

Open Terminal and run:

```sh
python3 --version
```

Python 3.9 or later is required. This is a Python runtime prerequisite, not a requirement to use the Codex CLI.

## 2. Register the local source

For the current source location, run:

```sh
python3 "$HOME/Desktop/projects/resume-after-reset-mvp/scripts/install_local.py"
```

This adds the plugin's source folder to `~/.agents/plugins/marketplace.json`. It preserves the marketplace's existing name, settings and other plugins, and backs up an existing file before changing it. It refuses a conflicting plugin name from another source. Keep the source folder at this location; moving it requires updating the registration.

The script only makes the plugin discoverable. It does not install it, enable it or approve hook trust.

## 3. Install in the desktop app

1. Finish active work, then fully quit and reopen the desktop app.
2. Open **Plugins** / the Plugins Directory and select the local marketplace source. A new marketplace created by this installer is named **Local Codex Plugins**; an existing personal marketplace keeps its existing name.
3. Find **Resume After Reset MVP** and install/enable it.
4. Review the plugin's local hooks through the host's hook-review controls and trust them if you want session-start reminders. Installation does not automatically trust hooks. Do not bypass review. If those controls are unavailable in your build, the main skill still works, but lifecycle reminders are not guaranteed; the saved continuation remains scheduled regardless of later messages.
5. Start a fresh local Codex chat. Select the plugin using the host plugin picker or type its skill name if offered. Ask: “Show Resume After Reset capabilities and my Unit-o-meter.”

The host copies local plugins to its versioned cache. After editing source, use the Plugins Directory's refresh/reinstall flow and reopen a fresh chat; merely changing the source does not prove the installed cache refreshed. Do not edit the cache manually.

## 4. Verify prerequisites

The agent should discover these plugin tools:

`resume_enable`, `resume_observe`, `resume_schedule`, `resume_retry`, `resume_attach`, `resume_status`, `resume_meter`, `resume_meter_state`, `resume_cancel`, `resume_deliver`, `resume_finish`.

It also needs native desktop `get_usage_limits` and `automation_update`. Namespace prefixes may differ. If the local server fails to start, inspect the desktop's MCP diagnostics for `resume-state`; check that Python is on the app's PATH and that `${PLUGIN_ROOT}` is expanded to the installed source directory. An unsupported host must report missing tools and stop setup, rather than pretend monitoring is active.

## 5. Enable per chat

Ask: “Enable Resume After Reset in this chat for the unfinished task. Warn me near my usage limit.”

Successful setup must return an armed registration plus a saved native automation ID. It stores the next reset gate. To see percentages, separately ask to show the Unit-o-meter. Only chats explicitly enabled are registered. The continuation timer is scheduled once at the next reported reset timestamp plus 60 seconds, and registrations expire after eight days. Setup reads the reset timestamp once and returns its plan from the same registration call; it does not need a separate usage-percentage observation or plan-building call. A fresh usage reading is required when the timer fires. A new one-shot is created only when a blocking reset changes or a due delivery needs a bounded retry. Later messages do not replace or cancel the saved request. Use the explicit disable action only if you want to stop its scheduled delivery.

Enable this before exhausting usage. Post-limit activation through an independent UI has not been verified.

## Updating and uninstalling

To update, refresh/reinstall through Plugins without disabling active registrations. Existing heartbeat prompts are snapshots from when they were created; after refreshing, ask the plugin to refresh the saved schedule so the host timer receives the new delivery instructions. Confirm status still shows the registration armed and attached.

To uninstall, first ask to disable continuation in each opted-in chat and confirm its native heartbeat is paused. Then uninstall/disable the plugin in Plugins. To remove it from the local marketplace as well:

```sh
python3 "$HOME/Desktop/projects/resume-after-reset-mvp/scripts/install_local.py" --remove
```

This retains plugin source, backups and SQLite data. Delete local data only if you no longer need registrations or test history. Pausing native automations is still required even if the source or local data is removed.

## Official installation reference

[OpenAI: install a local plugin manually](https://developers.openai.com/plugins/build/plugins#install-a-local-plugin-manually)

## Upgrading from 0.3

Disable the old registrations first so their recurring timers are paused, refresh/reinstall the plugin, then enable each desired chat again. Verify that status shows a saved scheduled_due and that its native schedule has a single occurrence. Do not leave the old 30-minute schedule active alongside the new one-shot.

## Battery meter (0.5)

After refreshing/reinstalling the local plugin, open a fresh local chat and ask “show my usage visually” once. This connects only the usage display; automatic continuation remains off unless separately enabled. Keep Unit-o-meter open in the conversation side panel. Its thread entrypoint should also appear among the conversation's content apps when the host supports Extensions. The exact panel persistence is controlled by the desktop app; the plugin cannot pin itself into the chat header.

The view refreshes every five seconds without another model request. New readings depend on the desktop recording quota events while work proceeds. If you close the panel, refreshing stops; reopening it resumes refresh. The local desktop must allow read-only access to its state index and sessions directory. Unknown layouts, inaccessible events and stale readings are shown explicitly.
