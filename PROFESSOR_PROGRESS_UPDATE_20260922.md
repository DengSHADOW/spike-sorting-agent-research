# Spike-Sorting Agent: Data and Experimental Update

**September 22, 2026**

Since reproducing the pipeline, I have audited the real data and established leakage-aware baselines.

**Data:** 17 current-format MAT files; 4,469,063 spikes; 16 supervised datasets; one unlabeled CH5 excluded; 1,374 expert actions; and 5,327 diagnostic images. Four Tianmin MAT files are exact Jacob duplicates and are not counted independently.

**Recording-block split:** 973 training actions (457 SPLIT, 374 DISCARD, 142 MERGE); 90 CH30 validation actions (43 SPLIT, 41 DISCARD, 6 MERGE); and 311 untouched final-test actions. The logs contain executed edits but no reliable KEEP or NOT_MERGE labels, so current evaluation measures expert-edit imitation rather than a complete stopping/merge-rejection policy.

**Protocol:** CH30 uses fixed expert pre-action states; predictions are scored but not executed. Split states use four diagnostic images, merge states use three pairwise images, and both include numerical metrics. All models see the same 90 samples and use strict action-only JSON. Open models ran with vLLM 0.29 on one H100 80 GB; GPT models used the official OpenAI API. No mock, RAG, MAT modification, or state update was used. Every formal run produced valid JSON for 90/90 samples.

**Action-level results (accuracy / macro-F1 / DISCARD recall):**

- Qwen3.5-2B: 0.067 / 0.333 / 0.
- Qwen3.5-4B: 0.378 / 0.530 / 0.024.
- Qwen3.5-9B: 0.500 / 0.570 / 0.
- Gemma-4-E4B-it: 0.544 / 0.578 / 0.
- GPT-4.1: 0.389 / 0.575 / 0; SPLIT recall 0.674.
- GPT-5.1: 0.367 / 0.564 / 0; SPLIT recall 0.628.

GPT-4.1 and GPT-5.1 agreed on 88/90 predictions. Their actual API versions were `gpt-4.1-2025-04-14` and `gpt-5.1-2025-11-13`. GPT-4.1 used 221,485 input and 530 output tokens; GPT-5.1 used 184,225 input and 18,438 output tokens, including 16,652 reasoning tokens.

**Numeric-only baseline:** Random Forest used log spike count, log overcluster count, ISI violation rate, and amplitude CV. Train-only grouped-CV accuracy/macro-F1 was 0.904/0.903. On CH30 SPLIT/DISCARD, it achieved 78/84 = 0.929. DISCARD P/R/F1 was 1.000/0.854/0.921; SPLIT was 0.878/1.000/0.935. All six errors were DISCARD→SPLIT. The reported 84/90 overall includes six positive-only MERGE cases handled by a constant rule; merge generalization remains untested.

The numeric model is supervised while all VLMs are zero-shot, so images have not yet been tested fairly. Next I will train matched Qwen3.5-9B images-only and images-plus-numeric LoRA/SFT models, add KEEP/NOT_MERGE supervision, run a safe rollout with ABSTAIN and reversible DISCARD, then evaluate the frozen model once on the 311-action final test.
