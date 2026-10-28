# SpikeAgent expert review — local backend and meeting demo

## Actual curation in the original page — 2026-10-27

The original `/` Expert review page now also executes curation through **Work on
this data** (or **Data & inputs → Open private working data**). No separate page
is required. Saved reviews remain an immutable-state annotation mode. Its action
panel offers **Preview / apply on a working copy** so a recorded proposal is not
mistaken for an applied operation.

- On Runpod, select the dataset and **Start / resume selected data**. A validated
  private package is loaded directly. The same dialog uploads a new MAT or
  imports a project-local MAT path through the existing protected importer.
- On free Firebase Hosting, import the private `.working.json.gz` package once.
  Processing and IndexedDB storage stay in this browser. **Start / resume locally
  loaded data** reuses the imported data without another large file import.
- Recording-start and historical-case entry both work. Revision-3 packages contain
  verified private atomic labels/trees for saved review states; actor input,
  checkpoint, full row membership and human-state hashes must agree. An older
  package cannot silently substitute the initial grouping for a historical case.
  All five private revision-3 packages are ready under
  `output/curation_review_local/working_inputs/`; none is in the public build.
- Single clusters offer Keep / Try split / Discard / Defer; comparisons offer
  Try merge / Keep separate / Discard / Defer. Preview shows both split children,
  or both original populations plus the projected merged group, with actual
  waveforms and projected ISI. ACG/CCG and descriptive quality metrics follow the
  projected assignments on demand. Confirm requires the waveform integrity check,
  a current preview, and the required preview groups to have rendered. The
  projected and applied assignment/tree hashes must be identical.
- Confirm updates actual per-spike membership through the atomic mapping,
  hierarchy, current cluster thumbnails, review progress and suggested comparison
  tasks. New/changed memberships need review again. Cancel changes no grouping;
  Undo restores the previous grouping and retains its audit record. Full/24-spike
  sampling, isolation, opacity, zoom/expand and common axes remain available.
- Saved judgments show Agent and User choice with Edit. Reasons, notes, positive
  Keep feedback, confidence, evidence exposure and matched Agent/reference
  provenance stay with the operation. Drafts and current selection restore only
  against the same working version. Reviewer workspaces are independent; explicit
  workspace import creates a new branch with contributor provenance. Shared
  browser data does not imply shared judgments.
- **Export sorting result** contains the real full-length assigns vector and
  hierarchy tree. **Export workspace** also contains private inputs, operations
  and versions. Existing History/Revisions include these working operations and
  reopen them in this page. Old standalone workspaces remain compatible.

Correction reason labels are now 57 edited, reusable phrases derived from the
156 existing Jacob source records: no author prefix or filler such as “This
waveform”. Original text/source records remain available; previous saved feedback
is not rewritten. New reasons are reusable after saving. Saved Agent objects
have a separate red outline; blue/magenta still identify the viewed comparison.
The duplicate middle Gallery / All waveforms mean-view tabs are removed.

The five approved MATs have no explicit native per-cluster sorter quality field
at the imported spikes/hierarchy level. The quality table states **Not provided
by input**; ISI is never substituted as a native quality grade. Existing model
records are shown only at matching states/objects. No inference, training,
harness change, independent-recording evaluation or paid API call is part of
this update.

Verified on real CH30: Cluster 1 split previews 331,733 + 739 members and creates
19 active clusters after confirmation; merging 211 into 1 gives 359,411 members.
Preview/application hashes agree, and Undo restores the original hash. Discard
removes exactly the selected 390 members; export retains all 430,937 original
row positions with updated labels. Refresh, edit/defer, History, all/24/isolation
controls and 1024/2560/390 layouts pass. Historical Cluster 305 starts with exactly
12,699 members and 17 active clusters, with its matched saved Agent suggestion;
a second reviewer starts independently using the same locally loaded data.
Checks: 73 Node tests, 56 reviewer Python tests, six private-input tests and five
exact-correlogram tests. Artifacts: `output/curation_inline_working_20261027/`.

## Meeting features integrated into the original page — 2026-10-27

The primary `/` Expert review page keeps the existing three panels. The left
queue is now a scrollable two-column cluster thumbnail grid inside the existing
dataset/file groups, retaining file progress, filters, reviewed/draft/action
badges and grouped historical states. Thumbnails use every member in the verified
PNG layers, never invented traces or means. They share state-level axes. The
active file appears first. Sort by spike count descending or descriptive
ISI <2 ms violation rate ascending (undefined/missing last); ISI is not a native
sorter quality grade. Sorting is restored on reload and loaded for newly opened
files. Click opens the exact review case. Shift-click selects a comparison only
when recording and full saved-state hashes match; cross-state/file comparisons
are rejected without changing the current review. Compare with remains available.

The middle **ACG / CCG** tab uses actual all-member spike times: ordered pairs
with B−A lag in [-50,50) ms, half-open 1 ms bins, raw counts and no time rounding,
mean curves or waveform sampling. ACG removes only same-event self-pairs and
keeps distinct coincident events. Single selection shows one ACG; a pair shows
both ACGs plus directional CCG. Each ACG uses its own labeled count scale so a
large cluster does not flatten the small cluster's structure. This is additional
reviewer evidence, recorded in exported review exposure; it is not ISI and does
not decide cell identity or automatically merge anything.

The right **Correction reason** dropdown generalizes the existing 156 Jacob
phrases into 74 distinct reusable templates without altering their original
source records or supplying a matched answer. Select **Type new / edit reason**
to write a new reason; saving remembers it for later clusters. Existing free text,
notes, autocomplete, reviewer isolation and feedback export remain available.
The free site stores new reasons with the current browser reviewer; the local
backend retains its existing shared reason library. These are not cloud sync.

Free publication adds only 94 aggregate correlogram snapshots for the existing
five channels / 262 cases, plus UI code. Raw MAT/NPZ, spike times, assignments,
private working packages and feedback are not published. All existing evidence
files are unchanged. Runpod's read-only correlogram endpoint matches the static
counts exactly. **Save review still records feedback, not actual Split/Merge
execution.** Operation preview/confirmation remains to be integrated into this
primary page. Agents/harness and model-call behavior are unchanged.

Verification: 72 Node tests, 55 existing/reviewer Python tests (including the new
service scope test), five exact-count correlogram tests; local static and published-site browser
checks for dataset groups, full thumbnails, both sorts, Shift pairing/rejection,
single/pair ACG/CCG, reason reuse, save/reload and 1024/2560/390 layouts. The
all/24 waveform switch regression also passes. Artifacts:
`output/curation_left_gallery_20261027/`.

## All / 24 sampled waveform switch — 2026-10-27

The original review page's **Cluster detail → Waveforms** selector offers
**All waveforms** (initial default) and **24 sampled / cluster**. It works for
single clusters and comparisons; the latter displays up to 24 actual members
per cluster (48 total for a pair), or all available members if fewer than 24.
Samples reuse the previously published evidence: real MAT rows evenly spaced
in recording-time order. No mean or SD band is added. Samples can miss outliers;
the coverage note and per-cluster drawn/total counts identify this mode.

Switching modes retains common full-population amplitude/time axes, colors,
opacity controls and the review target. The browser remembers the mode across
case/tab changes and reloads. Header Detail counts follow the selected mode.
This is a display preference and does not edit assignments or reviewer choices.
No new waveform values, raw packages or evidence files were published.

Local and published-site browser checks passed for all/sample counts, actual plotted coordinates,
isolation, opacity, zoom/expand, preference recovery, saved reviews and
1024/2560/390 layouts. The previous full-member regression also passed.
Both clusters' 24 × 45 sample values were independently matched against CH30's
original MAT. Verification: `output/curation_sample_toggle_20261027/`.

## Single-cluster coverage and clearer comparisons — 2026-10-27

The original `/` **Cluster detail** now uses the verified all-member PNGs for
single clusters as well as pairs. Previously only pairs used these layers;
single-cluster detail still showed the legacy mean plus at most 24 traces.
Cluster 305 now renders all 12,699 recorded members. Cluster 305 vs 463 renders
12,699 + 251 = 12,950 members. No waveform values, memberships or source evidence
images were generated or changed in this update.

Source and comparison clusters use blue `#0068c8` and magenta `#cf0064`. For pairs
with a population ratio above 4:1, the larger group starts at 45% opacity and is
drawn first; the smaller group starts at 100% and is drawn on top. Display alpha
is strengthened only for visibility. Controls show both groups or isolate either
one and adjust each opacity without changing the common axes or review target.
Counts explicitly identify hidden groups. Brightness is not a spike-count metric.

**Meeting scope in this primary page:** full single/pair detail, candidate ISI,
descriptive metrics, stored Agent explanations and saved reviewer feedback are
available. The sorted 2×4 cluster thumbnail grid, Shift-click pairing, actual
ACG/CCG, Split/Merge preview then confirmation with changed assignments, and a
generalized reason dropdown are still to be integrated here. Existing legacy
working-copy implementations below do not mean these are connected to `/`.
Saving a review here records feedback; it does not execute a sorting operation.
Gallery thumbnails remain mean summaries and their legacy expanded sample view
is not an all-member plot. Agent/harness behavior is unchanged.

Local static and published-site browser checks passed for exact single/pair counts, visible colors,
isolation, opacity, common axes, Gallery comparison selection, zoom/expand,
save/reload/History and 1024/2560/390 layouts, with no JavaScript errors or network
writes. All 72 existing Node tests passed. All 482 other public files, including
all evidence PNG/JSON files, are byte-for-byte unchanged. Verification and the
current Hosting build: `output/curation_contrast_single_20261027/` and
`output/firebase_free_share/20261027-review-contrast-single-v1/`.

## Primary review page and full pair waveforms — 2026-10-27

The primary interface is **`/` (`index.html`), Expert review**. The separate
Clusters / Current clusters entry and its banner were removed. Earlier standalone
working-copy pages remain accessible only for compatibility with existing local
workspaces and their History/exports; they are not required to review this chart.

In the original **Cluster detail → Compare with** chart, every individual member
waveform of the selected pair is overlaid on common time and amplitude axes.
The mean curves and ±SD bands are removed from this pair chart. Selecting Merge
uses this same plot, retaining both source colors and exact member counts.
Existing candidate ISI, descriptive mean-waveform correlation, reviewer choices,
Agent records, refresh recovery, History/Revisions and plot zoom remain intact.

All-member PNG layers are generated offline from approved raw inputs and verified
snapshot assignments, without row sampling. Immutable overcluster layers factor
all memberships exactly; combined unit layers avoid large browser downloads.
The free public site publishes only derived PNGs and minimal state/count/rendering
metadata, after the user's explicit authorization on 2026-10-27. Raw MAT/NPZ,
spike-value/time/assignment vectors, databases and feedback are not published.
There are five channels, 262 cases, 94 states and 172 distinct full-member unit
images. Cluster 305 vs 413 draws 12,699 + 1,286 = 13,985 waveforms.

Published verification: original-page Cluster 305 vs 413 renders all 13,985 waveforms, Compare/Merge share the same plot, target switching, zoom/expand, save/reload, History/Revisions and 1024/2560/390 layouts passed locally, in the prepared static build and online. Seventy-two Node tests, 24 backend tests and two all-row renderer regressions passed; seven deployed assets match. See `output/curation_original_review_20261027/FINAL_VERIFICATION.json`.

Offline regeneration (uses the existing plotting environment):

```sh
MPLCONFIGDIR=/tmp/review-pair-mpl .venv/bin/python -B mockups/curation-review/backend/build_pair_waveforms.py
# Once atomic layers already exist, rebuild only the compact public images:
MPLCONFIGDIR=/tmp/review-pair-mpl .venv/bin/python -B mockups/curation-review/backend/build_pair_waveforms.py --compact-only
```

Private cache: `output/curation_review_local/review_waveforms_full_v1/`.
Publication copies only its referenced PNGs and sanitized state metadata.
No model, agent/harness experiment, billing activation or feedback migration is
part of this change. Older standalone UI notes below describe retained legacy
capabilities, not the current primary navigation.


## Individual spikes are the default waveform view — 2026-10-27

**Current clusters → Waveforms** now opens a browser of individual spike
waveforms. Each tile contains exactly one original waveform, its one-based
source row, spike time and peak-to-peak amplitude, and expands independently.
There is no average, SD band or density image inside an individual tile.

- Every member is available, through 12/24/48-row pages, First/Previous/Next/Last
  and a page-number jump. Page totals and visible row ranges are explicit; this
  is full membership pagination, not random or representative sampling.
- Original row order is the default. Peak-to-peak amplitude descending computes
  over the complete selected membership to help inspect extreme waveforms.
- Compare and Merge preview retain separate browsers for both original groups.
  Split preview browses each projected child. Both sides share amplitude/time
  limits; the preview does not change the saved grouping until confirmation.
- The previous all-member overlay is now a secondary, initially collapsed view.
  It is painted only on demand and can still show every member; dense overlays
  naturally overlap. Member inspection no longer depends on reading that overlay.
- Only private packages with the full raw matrix provide complete coverage.
  Legacy packages explicitly report how many stored samples are available and
  never claim full membership coverage. No raw recording or package is published.

Verification: real CH30 first/next/last/jump pages, all-member amplitude ordering,
per-tile raw values equal the private source matrix, single-spike expansion,
Compare/Merge original memberships, Split children, confirm/cancel/undo/refresh,
History and 1024/2560/390 layouts passed. Twenty executor/activity Node tests
passed. Corrupt full matrices block confirmation and legacy sampled packages
are labeled explicitly. The published fixture check passed with no JavaScript
errors or non-GET requests; five deployed assets match the build. Free Hosting
only, billing disabled, zero new model calls. Private verification is in
`output/curation_individual_spikes_20261027/`.

## Full member comparison and split children — 2026-10-27

In **Current clusters → Waveforms**, selecting **Compare with** displays all
members of both clusters in an overlay and two individual panels on the same
time/amplitude limits. **Try merge** retains the original blue/orange populations
in those panels, so noise or outliers remain attributable before confirmation;
projected counts and ISI describe the combined target. **Try split** uses the
projected child memberships for both the overlay and the two child panels.
Only **Confirm** saves the operation. **Cancel preview** leaves the saved grouping
unchanged. **Opacity** adjusts individual trace visibility and **Zoom** adjusts
the amplitude viewport; neither samples or removes spikes. Complete rendering
requires the private full-waveform package; legacy sample-only inputs retain an
explicit warning. Snapshot archive summaries remain distinct from this working
interface and do not claim all-member rendering.

Validation: real CH30 comparisons draw 332,472 + 26,939 members; split children draw 331,733 + 739 in both overlay and individual panels on identical amplitude limits. Preview/cancel, confirmation/undo, refresh, opacity and 1024/2560/390 layouts passed. Published fixture checks passed with zero JavaScript errors or non-GET requests; all five changed assets match the build. Verification: `output/curation_pair_waveforms_20261027/`.

## Meeting workspace update — 2026-10-27

The supported working interface is **Curation → `/curation.html`**. The older
Snapshots page remains an optional immutable annotation archive. This release
implements the 2026-10-26 meeting layout in the existing working interface:

- A scrollable cluster thumbnail grid (four columns on desktop, two on mobile),
  sorted by descending spike count, with ISI <2 ms as a secondary key. An alternate
  sort puts the descriptive ISI rate first. Each card shows count and review status.
- Click selects one cluster; Shift-click selects a comparison, also available via
  **Compare with**. Main waveforms share axes and blue/orange identity colors.
  Smaller populations are painted last so a large cluster does not bury them.
- New private dataset packages contain **every member waveform**, encoded without
  precision loss: float32 only if exactly reversible, otherwise float64. A browser
  worker verifies the full waveform SHA256 and draws all members in responsive
  batches. Legacy packages still load but explicitly show stored samples rather
  than claiming complete waveforms. On Runpod, **Data & inputs → Load full waveform
  data from Runpod** upgrades the active input while preserving its working state;
  download the upgraded dataset/workspace to use it on Firebase.
- **Try split / Try merge** displays projected waveforms, counts and ISI before
  saving. **Confirm** alone executes and journals the change; **Cancel preview**
  leaves assignments, saved version and history untouched. Preview and execution
  share the same operation code and their resulting membership hashes must match.
- ISI, all-spike auto/cross-correlograms and descriptive metrics are on demand.
  Correlograms use 1 ms bins over [−50,+50) ms and exclude only self-pairs in ACG.
  Waveforms and histograms support expanded views and zoom. ISI is not a native
  sorter quality grade, and no new automatic quality threshold is imposed.
- Generalized Jacob reason options omit cluster IDs while retaining original-text
  provenance. **Type new…** reveals the text field. Confirmed new reasons are
  reused across reviewer profiles in this browser; Runpod also adds them to its
  shared library. Free-site users share across computers explicitly via **Data &
  inputs → Export / Import reason library**. This does not provide cloud sync.
- Gallery height and decision-panel width can be dragged. Session controls and
  references stay secondary; History/Revisions, undo, exports and independent
  reviewer workspaces remain compatible. Source MATs and saved Agent data are
  unchanged. No model inference or agent/harness experiment was started.

Private CH30 validation uses 430,937 original rows, with 1,614 already discarded
in its input and 429,323 current member waveforms. Cluster 1 splits from 332,472
into 331,733 + 739; confirmed state hashes equal the preview. Full raw working
packages remain private and are **not** included in the public Hosting build.

Validation: 72 Node + 54 review Python + six working-input/executor tests passed; local/static/public browser checks cover real CH30, preview/confirm, persistence, History/Revisions and responsive layouts. All five approved full-waveform private packages are prepared. Free Hosting is published; billing remains disabled. Detailed checks are recorded in `output/curation_meeting_20261027/VERIFICATION.json`.

## Review v2 draft — 2026-10-27

`/review-v2.html` is a draft of the simplified layout agreed in the 2026-10-26
meeting: cluster thumbnails with all stored waveforms (sorted by spike count,
then ISI rate), one workspace with blue/red overlay of up to two clusters,
metrics on demand, the stored AI decision with highlighted clusters, and a
human decision with projected SPLIT/MERGE results before confirm. It reuses
`curation-core.js` and keeps its own browser-local working copy. Requirements,
implementation status and open questions are in
[REVIEW_V2_DESIGN.md](REVIEW_V2_DESIGN.md). The supported Curation page is described above; this separate draft is not the published entrypoint.

## Working curation update — 2026-10-26

Open **Current clusters** in the top bar, or `/curation.html`, to execute real operations
on a separate browser-saved working copy. The original **Snapshots** page remains an
annotation/snapshot review: its **Save review** never changes spike assignments.
Older annotation-only descriptions below refer to that Review page.

- The primary workflow shows **Current clusters**, one row per current cluster.
  **Compare with** is always available when another active cluster exists. A single
  cluster offers KEEP / SPLIT / DISCARD; selecting a target offers MERGE /
  NOT_MERGE / DISCARD. Clear the target to return to single-cluster actions.
  DEFER remains unresolved. No Phase 1/2 completion gate controls these choices.
- Split changes memberships and marks both children for review; Merge changes
  memberships and marks the changed target for review. Accepted clusters remain
  inspectable/editable. Suggested pairs are optional, available from the start,
  and derived from waveform similarity, without an automatic merge threshold.
  Finish records pending clusters/pairs explicitly and never invents acceptance.
- **Agent is expanded by default**, including retained rationale, and can be
  collapsed. Past states are accessed through **Snapshots → Saved snapshots**,
  with Recording start / Past Agent decisions inside that optional disclosure.
  Historical states never become additional copies of a current cluster task.
- Both interfaces choose actions by the selected single/pair context. Snapshot
  annotations add `judgment_context` / `judgment_pair`, preserving the original
  experiment's phase and Agent pair. New comparison judgments cannot be falsely
  marked as agreement with an Agent suggestion for a different object. Old
  exports remain readable; these UI rules do not change the frozen experiment.
- Five approved channels provide 354 historical Jacob editing references. Both
  pages match them by complete pre-action state, phase and object; missing reasons
  or unrecorded KEEP/NOT_MERGE decisions are not synthesized. Reference viewing is
  tracked separately from Agent rationale viewing. Saved Agent suggestions appear
  only for exact matching states; changed states do not trigger inference.
- Working copies use IndexedDB, with version checks, undo and operation history.
  Reviewers start independently. **Export workspace** then explicit import makes
  a new continuation branch, retaining earlier contributors. This is not cloud
  synchronization. **Export sorting result** contains every original spike row's
  actual assignment, current hierarchy tree, source SHA256 and operation log.

### History and Revisions — 2026-10-26

Both Current clusters and Snapshots now expose a shared activity browser. It
loads all working copies owned by the current reviewer from IndexedDB, plus
that reviewer's saved snapshot annotations from the Runpod backend or free-site
browser adapter. Other reviewers' workspaces are excluded; explicitly inherited
contributions retain their actor and parent-workspace provenance.

- **History** lists working decisions/operations and the latest annotation for
  each saved snapshot, across datasets. Working operations and annotation-only
  records have explicit labels. Undo, superseded judgments and judgments for
  earlier memberships are distinguished, without changing the stored journal.
- **Revisions** includes each save/edit/undo/finish/continuation. Expand the
  record for spike counts before/after, judgment-field diffs, contributor, version,
  affected pair and state hashes. Undo links to its exact event/version, including
  KEEP edits with identical membership hashes. Older logs are projected from their
  event order; missing legacy values remain explicitly unavailable.
- Filter by dataset, workspace, record kind, action or exact numeric cluster ID.
  **More filters** adds contributor, Agent, status, UTC dates and grouping by date /
  workspace. Results have 25-record pagination and filters survive refresh.
- **Datasets & workspaces** shows current version, cluster count, pending count
  and continuation parent. **Open current workspace** opens the latest working
  state, without replaying an old edit. **Open recorded snapshot** opens the
  immutable case, including cases outside the currently selected dataset.
- **Export filtered JSON / CSV** downloads the matching records and provenance;
  the CSV escapes spreadsheet formulas. These reports are not resumable backups.
  Use **Export workspace** for working-copy transfer or full **Export feedback**
  for all annotation records and their saved event journal.

No record migration/reset, model request or cloud feedback synchronization is
performed. Free-site History reads annotation feedback only from this browser;
Runpod annotation feedback remains in SQLite and working copies remain in the
browser. Refresh can incorporate changes saved in another tab.

### Add a new working dataset

1. On the Runpod website, select **Curation → Data & inputs**. Choose an available
   dataset and **Start / resume working copy**, or add a new MAT as follows.
2. Under **Upload a new MAT dataset**, enter **Recording / session ID**, choose
   a local MAT and click **Upload MAT & validate**. For a file already on Runpod,
   enter its project-relative or absolute path in **Or use a MAT already on
   Runpod**, then click **Import server MAT & validate**.
3. The MAT must contain `spikes.Fs` (Hz), `spiketimes` (seconds), `waveforms`,
   `overcluster.assigns`, `hierarchy.assigns`, and `hierarchy.tree`. Conversion
   validates arrays and source SHA256; unknown source independence stays unverified.
   Uploads are capped at 256 MiB and conversion has CPU/memory/time limits.
   Protected recording paths and known protected hashes are rejected, even renamed.
4. After validation, click **Start / resume working copy**. The original MAT is
   read-only. Converted inputs are private under
   `output/curation_review_local/working_inputs/`; individual working progress is
   in the browser, not in the existing annotation SQLite database.
5. To use the free Firebase website, first click **Download working dataset
   package** on Runpod. At the public site choose **Curation → Data & inputs →
   Import working dataset / workspace**, select the downloaded `.working.json.gz`
   and click the import button. It executes locally in that browser. The public
   site has no raw-MAT converter or cloud working-copy synchronization.

The library audit found **zero verified unexposed, directly usable recordings**.
All known Jacob blocks have prior exposure; CH17 and CH20 share a recording block.
Chronux `spikes1` / `spikes3` are real raw samples but lack initial overcluster /
hierarchy assignments. They were not relabeled as valid new inputs. The private
audit is `output/curation_workflow_20261026/DATA_AUDIT.json`. A valid format alone
never establishes an independent, untuned evaluation recording.

The queue now browses all approved files through expandable file/view folders,
with per-file pending/reviewed entry counts and draft/saved action chips. User
choice cards restore saved actions, reasons and confidence/evidence tags; a
comparison shows the other cluster's matching saved review or an explicit
other-state/no-choice message. The Agent card and queue progress remain pinned.

Current interface update (2026-10-26): anonymous Agent 1 / Agent 2 selection is
inside the right yellow suggestion box. Cluster detail includes candidate-pair
comparisons; the former Compare pair tab is removed. Queue badges mark saved
reviews, header statistics are compact, and desktop panel borders can be dragged
to resize with browser-local width preferences. The lower-left Reviewer button creates,
renames and selects separate reviewer profiles. History shows current saved
judgments; Revisions shows actual save/edit/restore/undo events and field diffs.
Hidden Agent 3 supports a separate live preview through either OpenAI-compatible
or Anthropic interfaces. Its authenticated gateway is implemented but is not
configured or exposed by the free Hosting site; no paid request was made. See
[anonymous review and gateway setup](ANONYMOUS_AGENT_REVIEW.md).


The free public edition is at **https://spikeagent-review-20261023.web.app/**.
The current comparison uses anonymous Agent 1 / Agent 2, with Agent 3 as an
optional hidden live-model window (gateway unconfigured); see [anonymous review behavior and limits](ANONYMOUS_AGENT_REVIEW.md).
It serves matched historical responses and initial-cluster views, keeping each visitor's feedback in their browser;
see [free Hosting deployment and limitations](FIREBASE_FREE_SHARE.md).
The Runpod backend remains available separately. The [full cloud migration draft](FIREBASE_MIGRATION.md)
is paused and is not deployed.

The buildless frontend supports multiple imported Jacob datasets, saved Qwen9B decisions,
initial-cluster manual review without AI suggestions, and the separate six-case synthetic demo.

Real-data reviews are validated and persisted in SQLite on this Runpod workspace. The demo retains
its original browser-local v2 storage/export. Both sources include review, history and a conceptual
revision view. On the Review page, proposed actions are annotations. The separate Curation page executes working-copy edits; neither page starts model inference or learning.

## Local backend (2026-10-23)

Use the project-specific Python 3.12 environment; do not modify `/workspace/.venv`:

```sh
uv venv --python /usr/bin/python3.12 .venv-interface
uv pip install --python .venv-interface/bin/python -r mockups/curation-review/backend/requirements.txt
.venv-interface/bin/python -B mockups/curation-review/backend/import_jacob.py
.venv-interface/bin/python -B mockups/curation-review/backend/jacob_reasons.py
.venv-interface/bin/python -B mockups/curation-review/backend/server.py --port 8765
```

The importer is a one-time offline operation. Its default output must not already exist; use a **new**
`--output` child of `output/curation_review_local` for a fresh import and supply it to `server.py --bundle`.
Do not rerun against or delete the existing bundle to restart the website. The HTTP/SQLite layer uses the
standard library; input-based reviewer analysis also uses the dependencies above. It binds to `127.0.0.1`;
forward port 8765 in Remote SSH/VS Code, then visit
`http://localhost:8765`. The CLI importer above is optional when using the browser import manager;
the server can start with an empty catalog. Existing CH31 data is automatically registered when present.
The top selector remembers the selected dataset/view; `?source=demo` opens the demo.
Serving `dist/` alone still offers the demo and shows that the real-data backend is unavailable.

Initial verified import:

- Dataset: `cM2-e004_004-006_CH31`; 133,091 spike rows, 30 kHz, previously exposed development data.
- Run: `zero-shot baseline/runs/qwen9b_rollout_comparison_20261013_8000_thinking6000_v2/attempt_003/minimal/cM2-e004_004-006_CH31`.
- 67 saved decisions: 43 Phase 1 and 24 Phase 2; 26 unique pre-decision states; 191 unique verified PNGs.
- Cache: `output/curation_review_local/jacob_ch31_v1/` (about 31 MB of images/extra reviewer evidence).
- Feedback database: `output/curation_review_local/reviews.sqlite3`. Keep this file and the bundle when
  moving Pods. Both live on `/workspace` and are ignored by Git.

Only the six allowlisted MAT input fields are accessed: Fs, spiketimes, waveforms, overcluster.assigns,
hierarchy.assigns and hierarchy.tree. MAT-v5 decompression loads the struct locally without accessing
curation fields. The importer verifies MAT SHA256, input-array identity, checkpoint file and state hashes,
object/count alignment and all image hashes. It does not access expert terminal labels or reason annotations,
replay curation actions, or issue new model requests. Reserved/quarantined datasets are rejected before opening.
The historical request hash is preserved as **recorded**, with an explicit unverified-payload status.

Cluster detail shows one original waveform PNG. The other original Phase 1 four-PNG / Phase 2 three-PNG
inputs, including ISI histograms and amplitude distributions, remain in their corresponding tabs.
Overview, Gallery, comparison-cluster detail and amplitude-over-time
are additional reviewer evidence computed from the exact checkpoint membership and MAT spike rows.
Means use all members; extra traces use up to 24 chronologically spaced global row IDs, and amplitude-time
uses up to 500. These extra views are distinct from the frozen model's max-500 SHA256 sample. Missing spatial
coordinates or tree images are explicitly unavailable; no synthetic replacement is displayed in real mode.
Evidence is loaded on case selection, cached by state, and hashed again when served. The 67 cases are
independent historical pre-decision states: changing one review does not advance or rewrite that trajectory.

Reasons use a custom prefix combobox: entering `wav` suggests `Waveform…` but does not suggest
`Mixed waveform…`. Matching ignores case, and ↑/↓ + Enter or a click selects a candidate. Free text remains
available. Saving a disagreement remembers its reason on the server, collapses whitespace, deduplicates
case variants and tracks use count. Reasons are shared between reviewers. On connection, existing v2
browser reasons are imported idempotently with `browser-review-history` provenance; they are not claimed
to be Jacob's original annotations. Unsaved draft text is not added to the library.

Each browser has a persistent anonymous reviewer key (`spikeagent-reviewer-id`); the optional name is
a display/study label. Other computers share reasons but have separate review histories unless the reviewer
key is transferred deliberately. Keep this key or the JSON export to identify that browser's server records.
Save/undo uses revision checks to prevent silently replacing another tab's edit, and idempotency keys avoid
duplicate writes on retries. Failed saves retain the draft. Export includes reviewer identity, canonical
provenance, latest reviews, the append-only save/undo log and the reason library. Undo preserves history.
Demo reset does not erase real reviews. SQLite writes and reason updates are transactional.

API routes: `GET /api/status`, `/api/cases`, `/api/evidence/<case-id>`, `/api/assets/<sha>.png`,
`/api/reasons?prefix=…`, `/api/reviews`, `/api/export`; `POST /api/reviews`, `/api/reviews/undo`,
`/api/reasons/import`. Writes require same-origin JSON, a connection token and reviewer identity.
The server exposes only its explicit frontend files and imported assets, with no arbitrary project-file route.
It is intended for a local SSH tunnel, not public multi-user hosting.

### Dataset and input management (2026-10-23)

Open **Data & inputs** in the top bar. Choose a full dataset ID, the server's existing MAT or a local
file upload, and either initial-cluster manual review or the available saved Qwen9B decisions.
The review channels are CH31, CH3, CH20 and CH30 plus the explicitly authorized CH17 interface-only import; reserved/quarantined inputs
and unknown files are rejected. A filename alone never identifies a dataset. Uploads stream to a private
cache, are capped at 256 MiB, and must match the selected dataset's manifest byte count and SHA256 before
MAT parsing. Duplicate uploads/imports are reused; originals, old bundles and annotations are never replaced.

Import jobs run serially in a separate CPU process, persist progress/error/status on the workspace,
support cancellation, and become selectable without restarting the server. Closing the panel does not
stop an import. Interrupted jobs are marked explicitly after a server restart; retry creates a new job.
Only a successfully completed, validated bundle enters the catalog. Jobs have a 30-minute processing limit;
worker BLAS threads are limited to one. No model or GPU work is launched.

| Input | Imported review view | Meaning |
|---|---|---|
| `cM2-e004_004-006_CH31` | 67 saved steps / 17 initial clusters | Saved run completed; initial view has no AI decision |
| `cM2-e004_004-006_CH3` | 27 saved steps / 25 initial clusters | Saved run stopped at invalid SPLIT; no terminal score |
| `cM2-e004_011-015_CH20` | 12 initial clusters | No decisions in the selected saved Qwen9B attempt |
| `cM2-e008_021-028_CH30` | 18 initial clusters | No decisions in the selected saved Qwen9B attempt |

In manual mode, AI action/rationale/agent/agreement are null and model-visible evidence is empty.
KEEP may be saved without a reason; other proposals require a reason or note. Real waveform and ISI plots
are derived from the MAT initial hierarchy. Proposals still do not execute split/merge/discard.
Switching datasets isolates queue/progress/history and browser evidence caches; the feedback export contains
all of the current reviewer's real-data reviews, each with its own provenance.

The same manager also supports:

- Reason library JSON/CSV/UTF-8 text import (maximum 5000 reasons), case/whitespace deduplication and JSON export.
  CSV requires `text` or `reason`; text uses one reason per line. Imported reasons retain explicit user-import provenance.
- Feedback v3 JSON checking and restore. Provenance, source, phase, AI pair, action and evidence references are
  validated before an atomic import. Existing reviews, including undo tombstones, are skipped; revisions/history
  are not replaced. Original reviewer/timestamp are preserved under `imported_from`, and reasons are restored too.
- Server persistence for the name chosen with the lower-left Reviewer button. Copy/use the anonymous reviewer key to continue
  the same history on another computer; entering a display name alone does not change ownership.

Catalog, uploads, jobs and new bundles live under `output/curation_review_local/manager/`; SQLite remains
`output/curation_review_local/reviews.sqlite3`. Keep both on the persistent volume. The manager reads only
allowlisted manifest entries and accepts no arbitrary server paths, archive extraction, pickle, external URLs
or model endpoints. A new dataset/format outside the approved four needs a separately reviewed schema/scope change.

Additional API routes: `GET /api/datasets`, `/api/imports`, `/api/profile`, `/api/reasons/export`;
`POST /api/uploads` (raw MAT bytes), `/api/imports`, `/api/imports/cancel`, `/api/profile`,
`/api/reasons/import-file`, `/api/reviews/import` (`preview: true` checks without writing).
`GET /api/cases?bundle_id=…` selects an imported view. The same local-host, same-origin and write-token
rules apply. Backend tests use temporary fixtures; browser/input integration uses a separate SQLite database.

Queue/space update 2026-10-23: the left column is 20 px wider. Cases are grouped under the actual input
filename and full dataset ID, with combined input-file, phase, review-status and text filters. Status counts
reflect the file/phase/search scope; overall review progress remains session-wide. Filtering does not replace
the open case or discard a draft. Clear filters restores the full queue, and Save & next stays within the
current file/phase/search scope. Synthetic groups are explicitly labeled, with no invented MAT filenames.
Plot controls are compact overlays at the graph's lower-right corner, visible on hover or keyboard focus;
touch devices keep them visible. Gallery Expand follows the same pattern. The duplicate central model-input
button is removed; the upper-right input inspector remains accessible at narrow window widths as well.

Backend regressions (temporary fixture databases; no model/network calls outside loopback):

```sh
.venv-interface/bin/python -B -m unittest discover -s tests -p test_curation_review_backend.py -v
```

Remaining research integration: live model execution, executable expert operations, verified Jacob expert-annotation
trajectory/terminal alignment beyond the documented sources, additional dataset schemas outside the approved review
channels, public account-based hosting, and feedback-driven skill learning.

### Evidence navigation and chart explanations

Cluster detail contains one waveform chart in real saved runs, manual MAT review and the synthetic demo.
ISI/Timing, amplitude-over-time and tree evidence live in their own tabs. Saved Phase 1 amplitude histograms
have an Amplitude distribution tab for the source cluster; unavailable images do not get an invented replacement.
Compare with integrates a pair preview into Cluster detail while retaining the case's source.
A recorded single-cluster snapshot starts with no comparison; a recorded pair initially opens its saved target. Any comparison can now be selected or cleared independently of the recorded phase. The selected pair
is used for recomputed candidate ISI/quality metrics. A retained historical merged-ISI image always
belongs to the original AI pair. Split tree is shown only for Phase 1 cases with an actual saved tree
(or the clearly marked synthetic demo). Viewing is the only cluster-role chip and is mirrored on the
right for corresponding Agent/User choice cluster names. Dataset notes has been removed.

Chart notes explain axes, counts, sampling and line/dot/bar meanings. Saved raw-waveform PNGs have sampled
blue traces and a dashed zero line, with no mean overlay. New reviewer waveform views use a solid all-member
mean and up to 24 faint chronological traces. All-waveform curves have cluster-ID tooltips and a shared scale;
the Highlight curve selector and legend hover/focus isolate a curve because palette colors can repeat.
Gallery thumbnails also share time/amplitude scales within the checkpoint. Chart keys appear above the
waveform so they remain visible before scrolling through it. Original PNGs and hashes are unchanged.
Tab exposure, including Amplitude distribution, is retained in saved feedback.

### Candidate-pair analysis and quality metrics

The Compare with selector fixes the source to the case's review object and allows any different active cluster in the
same checkpoint as the candidate target. While proposing MERGE, its selector and the review-panel target
stay synchronized. Both means and ±1 population SD bands use common time/amplitude axes. Source is solid,
target dashed; colors remain tied to cluster IDs. No amplitude normalization or time shifting is applied.

The API independently verifies the approved MAT/input identity and historical NPZ file/state hashes, then
uses exact current member rows. Candidate ISI is recomputed from the sorted concatenation of both spike-time
arrays, without changing assignments. Histograms show 0–50 ms, but the violation denominator includes **all**
N−1 adjacent intervals. The numerator is strictly `< 2 ms`. The original saved-AI-pair histogram stays in ISI;
the candidate calculation is clearly separate. Actually displayed candidate pairs are retained in feedback.

Quality metrics reports population ISI count/rate, mean and SD of per-spike peak-to-trough amplitude, amplitude
CV (`std(ddof=0)/mean`), and mean-amplitude change between the first/last 20% of chronological spikes. The
drift windows are spike-count windows, not equal-duration time windows. Pair similarity is Pearson correlation
of all-member mean waveforms at recorded sample indices. Undefined denominators/constant means return null,
not zero. These are descriptive measurements with no automatic good/noise/merge threshold. Background-noise
SNR and spatial quality remain unavailable. Calculations do not read expert fields or invoke the controller.
The service holds at most one raw input dataset and 128 result objects; it never overwrites historical evidence.

New routes: `GET /api/analysis/<case-id>?cluster=<unit-id>` and `?target=<unit-id>` (target pairs use the fixed
case source). Both reject self/cross-state/out-of-scope objects before input access. Errors expose Retry in the UI.

### Confidence and missing evidence

Reviewers can optionally specify high/medium/low confidence and sufficient/insufficient evidence. Insufficient
evidence requires categories or an explanation: waveform, timing, amplitude, spatial, split tree, recording
context or other. Defer opens this request and remains an unresolved review status with `expert_action: null`.
Structured missing evidence can explain a defer without inventing a correction reason. A disagreement with an
available action remains a separate judgment. Confidence is self-reported, not a calibrated probability.

Additive v3 fields are `expert_confidence`, `evidence_sufficiency`, `missing_evidence`, `missing_evidence_note`,
and `pair_comparisons`. They survive save/reload/history/export/backup restore. Older records default to null /
unspecified / empty lists and remain valid. Server reconstruction validates enums and active pair membership.

### Verified Jacob default reasons and continuing additions

The separate `jacob_reasons.py` reference importer reads only selected worksheets of the original
`Jacob Bedke-Annotated_spike_sorting_data_w_chronux/action_sheet.xlsx`, pinned to SHA256
`b182b90d8a245b6e83a6ca8a9db57a59ef81cdd7442ee8d3045151d2bb501de7`. CH3/CH31/CH30 contribute 97/54/90
original rows: 241 references, 156 unique reasons after case/whitespace normalization. Spreadsheet quote wrappers
are stripped using the existing audited parsing convention; original unmodified cell text is retained in references.
CH20's old worksheet is excluded because its trajectory has a known invalid step and the canonical MAT log has
no corresponding text reasons. CH30 still has separate action/terminal provenance. No action replay is performed.

The seed is created once at `output/curation_review_local/jacob_reason_seed_v1.json`, ignored by Git alongside
the original annotations. The server's `--reason-seed` default imports it idempotently on startup. A missing
seed is shown explicitly in Data & inputs; it is never replaced by generated text. For a fresh machine, copy
the private seed or run the source importer after providing the verified workbook. Do not commit the workbook,
seed, SQLite database, or exported expert reason text to GitHub.

Each seeded reason keeps the worksheet/full dataset ID, row, original text, raw action and source-file hash.
This is **human-reference vocabulary**, not a matched answer for the current model checkpoint and not model
input. Prefix suggestions identify Jacob-original vs reviewer/imported reasons. Data & inputs shows seed status
and a shared-library browser with prefix/source filters and original reference details. Newly saved reasons are
deduplicated, prefix searchable and immediately shared between reviewers. Usage counts and existing entries
survive startup/import; new user text is never relabeled as Jacob. Canonical feedback records the selected
reason's known sources. Backup/file import does not trust a client's claim of Jacob attribution.

New routes: `GET /api/reasons/status` and `/api/reasons?prefix=…&limit=…` (1–5000). Reason JSON exports retain
source metadata. Keep the private seed with the persistent review data when moving Pods or hosting.

Offline measurement/source/persistence checks:

```sh
.venv-interface/bin/python -B -m unittest discover -s tests -p 'test_curation_review_*.py' -v
node --test tests/test_curation_review_interface.cjs
```

## Synthetic demo

Updated 2026-10-21 following the interface meeting. Suggested walkthrough:

Readability update 2026-10-22: system fonts replace the external font dependency. Body and form text
use 16 px on desktop and 18 px on wide screens, with darker secondary text, stronger control borders,
and clearer selected/disabled states. The three-column layout expands for 1920×1080 and 2560×1440;
smaller windows use stacked panels. Chart labels maintain a readable screen size as SVGs resize.
Gallery thumbnails emphasize curves and leave numeric axes to the expanded detail view.

Layout/plot update 2026-10-23: a compact header and footer leave more height for the workspace.
Desktop windows from 1000 px wide keep queue, evidence, and review in three independently scrolling
columns. Smaller screens stack bounded, independently scrolling panels. Each chart/image has +/−,
Fit, and Expand controls; Ctrl/Command + wheel zooms at the pointer, dragging pans a zoomed view,
and double-click toggles 2×/Fit. A focused plot also supports +/−, arrow keys, and 0/Home.
Ordinary wheel scrolling stays with the panel. Gallery waveforms have a separate Expand button.
These transforms change only the view and never alter data or review drafts.

1. Inspect Cluster 042. Click Disagree, change Discard to Split, enter a reason or note, and save.
2. Open the Cluster 008 case. Use All waveforms and Gallery to compare the three clusters in its
   synthetic recording. IDs, colors, population counts, AI objects, inspected object, and expert merge
   target are shown separately. Click a legend or thumbnail for cluster detail.
3. Disagree with Merge 008 + 021, keep the MERGE action, and choose target 034. The export retains both pairs.
4. For a KEEP case, either Agree without a comment, add optional positive feedback, or Disagree while
   keeping KEEP and marking Rationale or Evidence interpretation. A disagreement always needs a reason or note.
5. Enter a new correction reason. It becomes a searchable option on subsequent reviews in this browser.
   A frontend-only demo starts empty; with the local backend it can also browse the separately sourced shared library.
6. Open Cluster 031. SPLIT and agreement with the impossible scripted suggestion are unavailable.
7. Reload the page, review feedback history, undo a decision, and export the local JSON.
8. Open Revisions to discuss how feedback could lead to a validated workflow change. This view is a
   clearly labeled concept; it shows no fabricated evaluation results or live learning status.

Every case has its own synthetic recording and three comparison clusters. Overview/gallery never mix
channels or real datasets. Counts describe the synthetic cluster population; the detail view draws only
24 illustrative traces. Shared-axis overview templates and gallery/detail templates use the same generator.
No automatic discard threshold is applied, including for the illustrative 25-spike comparison cluster.

Feedback format is `spikeagent-human-interface-mockup-v2`. It records explicit agreement, action/target/
rationale/evidence disagreement types, AI rationale, AI and expert pairs, correction and positive comments,
recording/case/phase identity, optional reviewer ID, cluster counts, evidence exposure, and event history.
Real state/request/image hashes are **null**, because no real experiment or model request exists.
The anonymous `Agent_1` is a single scripted demo agent, not a second model comparison experiment.

`DEFER` is exported as `review_status: "deferred"` with `expert_action: null`. Expert proposals can include
MERGE in a phase-1 case or KEEP/SPLIT in a pair review, but are marked `phase_contract_compatible: false`
when outside the frozen backend phase contract. Nothing is sent to an executor. Pair reviews also offer
the backend's DISCARD action. The review object/source stays fixed while a different merge target or
comparison cluster is selected; inspecting another cluster does not silently change the review object.

Review text is escaped before display. All-waveform/gallery views, other inspected clusters, location
views, and opening the scripted AI rationale are recorded as evidence-exposure events. Extra reviewer
evidence is distinguished from the scripted AI-visible objects. These illustrative views are not the
main pipeline's Phase 1 four-PNG / Phase 2 three-PNG observations.

V2 saves to a new localStorage key. Existing v1 feedback is left untouched and included separately as
`legacy_v1` in exports, rather than being relabeled as new annotations. Reset clears v2 reviews/events
after confirmation but retains saved reasons and the v1 data. Storage failures leave a visible export warning.

Run the offline feedback-contract regressions with a Node.js runtime:

```sh
node --test tests/test_curation_review_interface.cjs
```

The synthetic demo remains separate from real saved-run ingestion, MAT processing and server-side reviews.
Jacob defaults are supplied by the separate expert-reference importer. Live model integration, executing
expert operations, public account-based hosting and skill learning remain separate integration work.

Questions for the meeting:

- Which evidence should the expert see first?
- What should require an explicit correction reason?
- Should labels and operations be separate controls?
- What confirmation should a real discard, merge, or split require?
- Which feedback should drive the next experiment?

The site is private by default. A local copy can be screen-shared during the meeting.

### Phase-specific review actions

Current single-cluster judgments allow KEEP/SPLIT/DISCARD (SPLIT requires recorded children).
A selected comparison allows MERGE/NOT_MERGE/DISCARD, with DEFER as a separate review status. Original Phase 1/2 labels remain in saved experiment provenance.
The editor, Core validator, browser adapter and local API enforce these limits.
Keep separate refers to the original recorded pair and selecting it restores that pair.
Verified older exports retain cross-phase proposals; editing them requires a current valid phase action.
No review action executes spike curation or a model request.

Saving a review now stays on that case and immediately closes its editor. The right
panel shows Agent and the saved User choice, with Edit to revise. Comparison-cluster
choices are nested within User choice. Navigate to another queue item explicitly.

The default Review view lists 82 initial clusters across five files, one task per
cluster. Historical agent cases is an optional separate view with independent
progress, retaining existing historical feedback. CH17 adds 10 manual tasks and
87,411 spikes; it has no linked Agent suggestion and no new model inference.

The green navigation rail has a top arrow to collapse/expand; its browser preference
is retained on reload. Without a preference, screens up to 1280 CSS pixels start
collapsed. Fluid typography and panel container queries adapt the 1K/2K layout,
including narrow queue filters and plot labels after resizing.

Agent suggestions start expanded on each page load, with the full retained rationale. Hide collapses the card; Show displays it again,
including any retained rationale, with no separate rationale toggle. Height dragging
remains available; double-click/Home/Enter or reopening restores full content.
