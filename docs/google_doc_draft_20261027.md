**Agentic-free zero-shot rollout (Opus 5.5, 10/26)**

**What it is**
* No controller and no Phase 1/2 scheduling: the model chooses every step itself. Actions: INSPECT / COMPARE / KEEP / SPLIT / MERGE / DISCARD / FINISH.
* Each turn the model sees only: the task and action definitions, a table of the active clusters, its own short action record (no rationale, no free notes), and the images it asked for in the previous turn.
* No skill, no examples, no expert information, no refusal fallback. Same plots, executor and scoring as the harness v1 minimal rollouts.

**Why it is purer than the controller v1 zero-shot**
* In v1 the controller decides which cluster to look at, in what order, which pairs to compare, and keeps unprocessed small clusters automatically; the model only answers single questions.
* Here all of that is the model's decision. Remaining harness choices (disclosed): SPLIT can only undo the last merge, fixed plots, turn limit 3200.

**Setup:** claude-opus-5-5, max output 8000 tokens, stateless request per turn; four development channels (already exposed, not held-out).

**Table 2. One-to-one F1 (IoU ≥ 0.5, Hungarian, full output)**

| Channel | Turns | F1 | P | R | Expert units found | Expert spikes discarded | v1 Opus | v1 Astra |
|---|---:|---:|---:|---:|---|---:|---:|---:|
| CH31 | 543 | 0.577 | 0.405 | 1.000 | 3/3 | 0 | 0.717 | 0.477 |
| CH3 | 343 | 0.382 | 0.236 | 1.000 | 2/2 | 0 | 0.416 | 0.250 |
| CH20 | 269 | 0.405 | 0.256 | 0.967 | 1/1 | 0 | 0.000 | 0.361 |
| CH30 | 1258 | 0.343 | 0.213 | 0.887 | 3/3 | 0 | 0.644 | 0.203 |
| **Mean** | — | **0.427** | — | — | **9/9** | **0** | 0.444 | 0.323 |

Floors and ceiling: no curation 0.155 (mean); restricted oracle 1.0.

**Findings**
* Opposite error types. Controller v1 Opus found 6/9 expert units and discarded 58,141 expert spikes (it deleted whole mixed clusters in Phase 1). Agentic-free Opus found 9/9 and discarded none.
* Precision is low because noise fragments are kept: 461 pure-noise clusters survive across the four channels (234 on CH30).
* Heavy over-splitting and few deletions: CH31 has 125 SPLITs vs 20 by the expert; CH3 has 27 DISCARDs vs 60 by the expert. Almost no COMPARE/MERGE.
* The strategy is the same on every channel: inspect and split clusters in ID order until they are small, then KEEP them one by one, then FINISH.
* Single run; the differences from v1 are not yet a firm conclusion.

**Cost**
* $221 at standard prices ($274 under conservative accounting), 2,413 API calls; CH30 alone about $197.
* Cost is almost all input and grows roughly with the square of the number of turns, because the cluster table and action record are resent every turn.
* CH30 stopped at the budget limit four times; each resume replayed earlier turns byte-identically without billing, so the trajectory is equivalent to one uninterrupted run.

**Next**
* Free, offline: on-policy error decomposition, i.e. how much F1 is lost to keeping noise fragments vs to over-splitting.
