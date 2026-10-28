# Review interface v2 — design notes from the 2026-10-26 meeting

Draft page: `dist/review-v2.html` (served by the local backend at `/review-v2.html`).
It is a separate page; the existing Snapshots and Current-clusters pages are unchanged.

## What the meeting asked for

Participants: Tianmin, Shreesh, Jacob, Zhuoxuan, Tengyang. Conclusion: the current
interface is too complicated and does not show the features the lab needs.
Keep only the following.

### 1. Cluster thumbnails (the main view)

- One small box per current cluster, in a scrollable grid. Two rows of four
  visible at a time; scroll for the rest.
- Each box shows **all waveforms of the cluster overlaid** (not the mean, not a
  mean ± SD band), the number of spikes, and a quality metric.
- Order: decreasing number of spikes, then decreasing quality. Small clusters
  (a few hundred spikes) usually end up merged or removed, so they go last.
- No separate cluster list on the left.

### 2. Workspace (one large panel)

- Click a thumbnail: all waveforms of that cluster appear in the workspace (blue).
- Shift-click a second thumbnail: its waveforms are overlaid in a second color
  (red). At most two clusters at a time.
- Per selected cluster, show detailed metrics on demand: ISI histogram and
  cross-correlogram first; PCA and others later.

### 3. AI decision

- Show the AI's action for the current state and **highlight which clusters it
  concerns** (red outline on the thumbnails).
- Show its rationale.

### 4. Human decision

- Select the cluster(s) and choose an action: KEEP / SPLIT / DISCARD for one
  cluster, MERGE / NOT_MERGE / DISCARD for two.
- **SPLIT and MERGE show the projected result** (waveforms and the ISI histogram
  after the operation) **before** the human confirms. Confirm applies it; reject
  discards the preview. KEEP and DISCARD are single-step, still with confirm.
- Reason: a drop-down of **generic** reasons (derived from Jacob's annotations,
  without cluster IDs) plus "Type new…". A newly typed reason is added to the
  drop-down for later rounds.

### Out of scope for now

Navigation rail, tabs, snapshot review, agreement buttons, confidence, notes,
reviewer management, history/revisions browser.

## What the draft implements

| Requirement | Draft |
|---|---|
| Thumbnails, 2×4 visible, scroll | Yes; sorted by spikes desc, then ISI violation rate asc |
| All waveforms per cluster | Every stored member trace (12 per atomic component, i.e. tens to a few thousand per cluster), decimated to at most 600 lines per plot. See open question 1 |
| Quality metric in the box | ISI < 2 ms rate as placeholder. See open question 2 |
| Click / shift-click, blue / red overlay | Yes, shared amplitude axis (toggle in the header) |
| Metrics on demand | Spikes, ISI rate, amplitude mean ± SD, CV, drift; ISI histogram per cluster; for a pair: mean-waveform correlation, ISI if merged, cross-correlogram (±50 ms, 1 ms bins) |
| AI decision with highlighted clusters | Stored suggestions matching the exact current state (Agent 1 / Agent 2 bundles); red outline on involved thumbnails; click a suggestion to select its clusters. No live model calls |
| Projected SPLIT / MERGE before confirm | Yes, computed on a copy; confirm applies, reject clears |
| Generic reasons + type new | Yes; new reasons persist in the browser |
| Undo, export sorting result | Yes |

Operations run through the existing `curation-core.js` working copy
(split = undo the cluster's last hierarchical merge, merge, discard), so the
exported result has the same schema as the Current-clusters page.

## Open questions for Tianmin / Shreesh

1. **"All waveforms."** The working package stores 12 raw traces per atomic
   component. A 40,000-spike cluster therefore shows a few hundred traces, not
   40,000. Options: (a) keep this, state "N of M traces drawn"; (b) raise the
   stored sample to ~500 per cluster, which the observation module already does
   for the AI; (c) render full overlays server-side as images. (b) is the
   cheapest change that is visually dense.
2. **Quality metric.** The lab's spike-sorting software metric is not in the MAT
   files we receive. Until it is exported, the thumbnail shows the ISI violation
   rate. Which metric should it be?
3. **AI decisions.** The page shows stored decisions only when the current state
   exactly matches a state the agent has seen. For the live study, a backend
   agent must answer for the reviewer's current state; that is the "one pass
   with a back-end agent" Tianmin mentioned for Wednesday.
4. **Generic reason list.** The eight seed reasons were drafted from Jacob's
   texts; they need a pass from Jacob.
5. **Ordering tie-break.** Spikes first, then quality, as Shreesh proposed. Jacob
   mentioned possibly more nuanced rules later.

## Evaluation metrics discussed (for the proposal)

- Time to train: from downloading the naive agent to the lab accepting its
  performance on a held-out, human-scored piece of the same study.
- Time to deploy: how long the trained agent takes on the rest of the study.
- Accuracy against human scoring; subjective ease-of-use ratings per lab.
- Each dataset split into a training piece, an evaluation piece scored by a
  different lab member, and the deployment remainder. A separate phase uses
  people and datasets never involved in development.
