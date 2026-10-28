# Agentic-free v0 rollout — Claude Opus 5.5

Run: `agentic_free_v0_opus55_20261026_v3`; campaign status: **stopped**.

No scheduler: Opus chose every INSPECT / COMPARE / SPLIT / MERGE / DISCARD / FINISH. Renderer, executor and scoring are those of the engineering_v1 minimal rollouts, but this is a different condition: report it separately. No skill, examples, expert information or refusal fallback. Exposed development channels.
Manifest SHA256: `a6b9a48ddf5177f16c9c1797f35562a1d825532c60cca9bc868d765b73d104b3`.
Calls: 209; known standard estimate: $8.348140; conservative usage/reservations: $10.210630.

| Dataset | Status | Turns / limit | Actions | Not executed |
|---|---|---:|---|---:|
| cM2-e004_004-006_CH31 | stopped | 209 / 3200 | COMPARE 2, DISCARD 12, INSPECT 128, MERGE 1, SPLIT 66 | 0 |
| cM2-e004_004-006_CH3 | not-started | 0 / — | — | 0 |
| cM2-e004_011-015_CH20 | not-started | 0 / — | — | 0 |
| cM2-e008_021-028_CH30 | not-started | 0 / — | — | 0 |

No channel reached FINISH; nothing is scored.

Incomplete channels are not final scores. No automatic retry or prompt update.
Stop/error: `{'type': 'KeyboardInterrupt', 'cause': None, 'message': None}`. Local records: `../../runs/agentic_free_v0_opus55_20261026_v3/`.
