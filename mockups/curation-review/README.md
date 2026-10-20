# SpikeAgent expert review — meeting mockup

Buildless, interactive human-interface concept. Open `dist/index.html` directly, or serve `dist/`
with a local HTTP server. All cases, plots, numbers, and AI suggestions are synthetic.

The interface includes an expert-review workspace, feedback history, and a conceptual workflow-revision view.
Reviews are stored in browser localStorage and may be exported as JSON. No model APIs, recording files,
backend, or learning jobs are connected. No spike assignments are changed.

Suggested walkthrough:

1. Inspect Unit 042. Change Discard to Split, select a reason, and save.
2. Open Units 008 + 021. Compare waveform and timing evidence, then choose Merge, Keep separate, or Defer.
3. Open Unit 031. Notice that Split is unavailable because it is a leaf node.
4. Review feedback history, undo a decision, and export the local JSON.
5. Open Revisions to discuss how feedback could lead to a validated workflow change. This view is a
   clearly labeled concept; it shows no fabricated evaluation results or live learning status.

Review notes are escaped before display. Exported feedback includes a synthetic-data notice, model
proposal, reviewer action, reason, evidence ID, viewed evidence tabs, and the full save/undo event log.
Opening the location view is recorded as additional reviewer evidence that was not in the scripted model input.

Questions for the meeting:

- Which evidence should the expert see first?
- What should require an explicit correction reason?
- Should labels and operations be separate controls?
- What confirmation should a real discard, merge, or split require?
- Which feedback should drive the next experiment?

The site is private by default. A local copy can be screen-shared during the meeting.
