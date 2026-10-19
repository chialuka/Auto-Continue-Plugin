# Test the plugin

The runtime and Python test suite use only the standard library. The tests do not require a real Codex installation, a login, or a running background service. Node.js is needed for UI checks.

```sh
python3 -m unittest discover -s tests -q
node tests/test_ui.js
python3 scripts/check_package.py
```

The flow benchmark additionally supports optional `tiktoken` for a clearly labeled payload-token proxy:

```sh
python3 benchmarks/flow-comparison/compare.py --no-tokenizer
```

Without `--no-tokenizer`, install `tiktoken` into your own isolated Python environment first. Neither mode makes model requests or creates production timers. The harness mocks usage/service boundaries and stores registrations in temporary directories.

Real tests require macOS, a signed-in Codex executable with app-server and queue support, and explicit authorization to deliver a harmless message into an exact test chat. Use `direct_service.py smoke` as documented in README. Do not label transport smoke tests as quota recovery. Record failures and untested physical conditions honestly.

For a clean install, verify direct enable, status, cancellation, idempotency, restart persistence, absence of duplicate delivery, code-generated local time, and service removal. Check that no native and direct timer are armed together in the same chat.
