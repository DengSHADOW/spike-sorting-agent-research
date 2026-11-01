# Instructions for AI coding agents

Read `docs/AGENT_GUIDE.md` before anything else. It covers the current state, where the truth is,
the environment, the runbook, rules and known pitfalls. The authoritative, maintained document is
`项目总文档.md` (Chinese): current design in §3.7.13, harness results in §5.7, pods and live runs
in §9, open decisions in §7, change log in §10.

Rules that matter most (full list in the guide, section 8):

- Default to read-only. Runs, deletions, paid API calls, pod changes and pushes need the project
  owner's agreement. Push only to GitHub `main`, and ask when unsure.
- Never put expert labels, expert reasons or scores into model inputs.
- Do not overwrite historical outputs, frozen prompts, frozen configs or old skill versions; new
  runs go to new `output/` directories, new skills to new files.
- While a rollout runs, do not edit the files in `FROZEN` (`scripts/run/run_local_rollout.py`) or
  the skill in use. Both RunPod pods share this disk.
- Never `git add -A`: `spike-sorting-private-backup-20261019/` is not ignored and holds keys.
- Leave `mockups/curation-review/` (the website) alone unless asked; another session maintains it.
- Call every harness v2 number a development-set result: the four channels were used for tuning.
