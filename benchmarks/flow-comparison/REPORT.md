# Direct chat versus panel setup

Measured with actual production handlers and meter JavaScript, fixed synthetic inputs, temporary stores and mocked usage/service boundaries. No model request, production timer or queue message is created.

## Controlled before/after comparison

The same fixtures were run against version 1.0.1 and the optimized 1.0.2 source:

| Path | 1.0.1 payload proxy tokens | 1.0.2 payload proxy tokens | Agent turns required |
| --- | ---: | ---: | ---: |
| Direct, backend generic instruction | 305 | 85 | 1 |
| Direct, explicit instruction | 347 | 127 | 1 |
| Open panel through chat, then Enable | 243 | 243 | 1 |
| Reuse an already-open panel | 0 | 0 | 0 |

Successful variants saved the same exact instruction and due time, with one usage read in code. Version 1.0.2 removes internal job/account/receipt details from the enable confirmation; a status request still provides detailed state when needed. The panel preserves its edited message when receiving the compact result.

The proxy counts JSON tool arguments plus text results using local `o200k_base`. They exclude shared chat history, hidden reasoning, tool schemas, user prompt text, final output and provider caching/accounting. They are not total billed tokens or an exact GPT-6 billing tokenizer measurement. No model latency or real desktop rendering rate was measured.

Opening a panel still needs an initial agent turn. Reuse of the same bound panel needs none. If the host never supplies the exact chat-binding input, the button safely remains disabled; this simulated dependency is not a measured failure rate for the real host. Missing passive meter readings do not prevent direct enablement when the panel is correctly bound.

Recommendation: use direct activation as the primary onboarding command. It now has a smaller visible tool payload than opening a normal populated panel and avoids that UI dependency. Keep the panel optional for subsequent control. Skill instructions request one immediate setup call and one confirmation, but cannot force zero model reasoning.

## Reproduce

Run `python compare.py` using a Python environment with optional `tiktoken` installed and Node.js available. `python compare.py --no-tokenizer` performs the same behavioral checks and counts bytes without inventing token estimates. `results.json` and `baseline-1.0.1.json` contain the controlled results.

The harness is portable relative to the repository root. Optional `RESUME_FLOW_PLUGIN_ROOT` selects another installed/source version for comparisons and `RESUME_FLOW_RESULTS` selects the output file. Runtime/usage access is mocked even when comparing another installed version.
