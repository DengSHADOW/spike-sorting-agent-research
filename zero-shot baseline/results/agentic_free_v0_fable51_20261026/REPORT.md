# Agentic-free v0 rollout — claude-fable-5-1

Run: `agentic_free_v0_fable51_20261026`; campaign status: **stopped**.

No scheduler: the model chose every INSPECT / COMPARE / SPLIT / MERGE / DISCARD / FINISH. Renderer, executor and scoring are those of the engineering_v1 minimal rollouts, but this is a different condition: report it separately. No skill, examples, expert information or refusal fallback. Exposed development channels.
Manifest SHA256: `414463f2c7e9976957e4c80fefed8055d8ffec1bbabfeff70d2b1db4c4485c34`.
Calls: 114; known standard estimate: $9.284500; conservative usage/reservations: $12.214412.

| Dataset | Status | Turns / limit | Actions | Not executed |
|---|---|---:|---|---:|
| cM2-e004_004-006_CH31 | stopped | 114 / 3200 | DISCARD 2, INSPECT 64, SPLIT 47 | 0 |
| cM2-e004_004-006_CH3 | not-started | 0 / — | — | 0 |
| cM2-e004_011-015_CH20 | not-started | 0 / — | — | 0 |
| cM2-e008_021-028_CH30 | not-started | 0 / — | — | 0 |

No channel reached FINISH; nothing is scored.

Incomplete channels are not final scores. No automatic retry or prompt update.
Stop/error: `{'type': 'KeyboardInterrupt', 'cause': None, 'message': None}`. Local records: `../../runs/agentic_free_v0_fable51_20261026/`.
