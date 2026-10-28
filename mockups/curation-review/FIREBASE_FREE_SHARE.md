# Free Firebase Hosting edition — 2026-10-23

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



**2026-10-26 update:** the active release now uses anonymous Agent 1 / Agent 2
and a hidden Agent 3 live-model window (gateway not configured), with 90 matched CH30 cases per primary agent and five
manual views. Earlier 9B snapshots remain private deployment archives. See [the current anonymous comparison](ANONYMOUS_AGENT_REVIEW.md).

Public website: **https://spikeagent-review-20261023.web.app/**.

Anyone with the link can open the site without SSH or an account. Firebase serves
the snapshot independently of the Runpod process. The project is
`spikeagent-review-20261023`; billing was verified disabled before publication.
Only Firebase Hosting is deployed, on the Spark free plan. No Cloud Run, cloud
functions, database, authentication service, GPU or model client is connected.

Published verification: original-page Cluster 305 vs 413 renders all 13,985 waveforms, Compare/Merge share the same plot, target switching, zoom/expand, save/reload, History/Revisions and 1024/2560/390 layouts passed locally, in the prepared static build and online. Seventy-two Node tests, 24 backend tests and two all-row renderer regressions passed; seven deployed assets match. See `output/curation_original_review_20261027/FINAL_VERIFICATION.json`.

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

## Full member comparisons — 2026-10-27

The working Waveforms tab now keeps the two original populations distinct during Merge preview, with a full-member overlay and separate panels sharing amplitude/time limits. Split preview shows both projected children in the overlay and separate panels. Opacity controls help inspect sparse traces without sampling the input. Existing working copies, preview/confirm semantics and private package scope are unchanged.

Validation: real CH30 comparisons draw 332,472 + 26,939 members; split children draw 331,733 + 739 in both overlay and individual panels on identical amplitude limits. Preview/cancel, confirmation/undo, refresh, opacity and 1024/2560/390 layouts passed. Published fixture checks passed with zero JavaScript errors or non-GET requests; all five changed assets match the build. Verification: `output/curation_pair_waveforms_20261027/`.

## Meeting workspace — 2026-10-27

Direct working interface: **https://spikeagent-review-20261023.web.app/curation.html**.
It now has the scrollable cluster grid, shared-axis full member waveform viewer,
click/Shift-click comparison, Try split/merge preview then explicit confirmation,
on-demand ISI/correlograms/metrics, and reusable generalized reason choices.
Preview cancellation never saves an operation. Existing browser workspaces,
History/Revisions and exports are retained.

All-member waveform viewing requires a new full-waveform working package.
Runpod converts these privately and stores float32 only when exactly lossless,
otherwise float64; the browser verifies the raw matrix SHA256 before use. Older
packages remain readable with an explicit stored-sample warning. Upgrade on
Runpod via **Data & inputs → Load full waveform data from Runpod**, then export
and import the package/workspace on the public site. New visitors must import a
private package to start working; opening a public snapshot does not supply one.
No full waveform matrices or private working packages are published to Hosting.

New reasons are reusable by reviewer profiles in the same browser. **Data & inputs
→ Export / Import reason library** transfers them between computers; choices and
recordings are not part of that vocabulary export. Runpod additionally syncs new
confirmed reasons to its existing reason endpoint. Free Hosting has no automatic
multi-user reason, feedback or workspace synchronization. No cloud service,
model inference or agent/harness change is part of this release.


Meeting-workspace release verification (2026-10-27): **72 Node tests, 54 existing
review Python tests and six working-input/executor Python tests passed**. Browser
checks passed locally, against the prepared static build and at the public URL:
full matrix integrity, every current CH30 waveform, preview/confirm equality,
comparison and correlograms, generalized reasons, refresh recovery, independent
reviewers, History/Revisions and exports at 1024/1920/2560/390 widths. Corrupt
full matrices block confirmation; histogram expand/zoom and vocabulary JSON
transfer also passed. Public checks observed zero JavaScript errors and zero
non-GET requests. Thirteen deployed assets match the build; all 198 previously
published evidence/reference files are unchanged. All five approved private
full-waveform packages are prepared, and all five original MAT hashes match.
Billing remains disabled; only free Hosting was deployed. Verification is in
`output/curation_meeting_20261027/VERIFICATION.json` (private, not published).

## Working curation — 2026-10-26

Open **Curation** or `/curation.html`. The free site now executes Split/Merge/
Discard and updates current cluster/comparison tasks in a private browser working copy. Single/pair actions follow Compare with immediately, without a phase gate.
First prepare/download a working dataset package on Runpod, then choose **Data &
inputs → Import working dataset / workspace** here. The package and changed
assignments stay in IndexedDB; they are not posted to Firebase. **Export sorting
result** contains actual spike assignments/tree; **Export workspace** transfers
or explicitly continues work on another reviewer/computer. The old Review page
continues to annotate immutable historical/initial snapshots.

354 Jacob editing references are matched by exact pre-action state and object,
separate from Agent suggestions. No new inference is started. New working-copy
states without a saved matching suggestion show that none is available.

Raw MAT conversion remains on Runpod. The public release does not contain any
private working dataset package or full per-spike working vectors. Consequently,
published snapshot views alone cannot be started as working copies without an
explicit private package import. See [dataset buttons and required MAT fields](README.md#add-a-new-working-dataset).

Latest verification: **59 Node + 54 Python review + five executor/input tests passed**. Published browser checks passed for context actions, real CH30 early merge/split reversal, saved pair identity, original Agent provenance, refresh/export/restore validation and 1024/1920/2560/390 layouts. No JavaScript errors or public feedback network writes were observed. Ten published frontend assets match the prepared build.

The current context-actions release makes Agent expanded by default and moves
Recording start / Past Agent decisions into optional Saved snapshots. Current
clusters is the working workflow. Compare is available immediately; clearing it
restores single-cluster actions. Merge/Split refresh actual memberships, review
status and evidence. Legacy workspaces/feedback remain loadable; new snapshot
judgments preserve their context/pair separately from recorded experiment phase.

Activity release verification: **67 Node + 54 Python review + five executor/input tests passed**. The public browser check covered four datasets, three workspaces (including continuation), filters, 25-record pagination, undo deltas, revised reasons, filtered JSON/CSV, cross-page navigation, refresh recovery and reviewer isolation at 1024/1920/2560/390 widths. No JavaScript errors or feedback network writes were observed. Eight published assets match the prepared build; existing evidence/references are unchanged.

History / Revisions now combine the current reviewer's browser working copies
and snapshot feedback, across datasets, with filters, grouping, pagination,
per-version before/after changes, continuation lineage and filtered JSON/CSV
reports. Working edits and annotation-only judgments are labeled separately.
The public Curation page loads the same read-only static adapter when viewing
activity; feedback, working inputs and reports are never uploaded to Firebase.
Three activity frontend files are added; the approved public evidence/references
are unchanged. Existing browser keys and the IndexedDB schema are retained.

## Available behavior

- Seven approved real-data views: Agent 1 / Agent 2 each have 90 matched CH30
  states; manual initial-cluster views include CH31 (17), CH3 (25), CH20 (12)
  CH30 (18) and the explicitly authorized CH17 (10). There are 262 cases total,
  with 82 initial-cluster tasks in the default queue. The 90 historical cases per
  agent are optional and have separate progress.
- Original saved evidence and reviewer waveform/gallery/timing/amplitude views,
  hover/zoom controls, filters, and the separate synthetic demo.
- Precomputed quality statistics and candidate comparisons for all available
  review-source/target choices: 94 states, 1,954 distinct candidate pairs.
  Statistics use the original all-member calculations, not sampled trace counts.
- All 156 Jacob default reasons with original provenance. New reasons, reviewer
  names, judgments, confidence, missing evidence, revisions and undo history are
  saved in this visitor's browser. JSON export/restore transfers feedback manually.
- Browser writes fail explicitly if storage is unavailable/full; they are not
  reported as server saves. Supported browsers use Web Locks for cross-tab saves.

Feedback is **not shared or synchronized** between visitors/computers. The same
name is a display label, not a login. Clearing browser storage removes local
records; export JSON to retain them. MAT uploads and server import jobs are hidden
in this edition. New published snapshot views require a new offline build and publication. Private working datasets can instead be imported by each visitor without redeployment.
The main Runpod version still provides its existing SQLite-backed workflow.

## Publication boundary

The working-curation release contains 213 public files, about 59.12 MiB. It contains frontend HTML/CSS/JavaScript,
approved derived PNG evidence, sampled reviewer evidence JSON, case catalogs,
precomputed aggregate statistics and the verified reason vocabulary. Those files
are publicly accessible. Raw MATs, checkpoints/NPZ, workbook, databases, current
user reviews, private uploads, credentials, Git metadata and unfinished full-cloud
implementation files are excluded.

The browser adapter is `dist/static-share.js`; only the generated public edition
loads it. The normal local HTML does not load the paused Google-login draft.
`build_free_share.py` uses the read-only imported catalogs and existing analysis
functions, verifies source hashes/state alignment, and writes a new directory.
The builder does not change historical evidence or call a model. Expert edits are replayed offline only to verify reference-state hashes; interactive working operations run later in the visitor’s browser.

## Rebuild and deploy

From the repository root, use a **new** build directory each time:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv-interface/bin/python -B \
  mockups/curation-review/backend/build_free_share.py \
  --output output/firebase_free_share/CHOOSE_A_NEW_BUILD

/root/.local/bin/firebase deploy --only hosting \
  --project spikeagent-review-20261023 \
  --config output/firebase_free_share/CHOOSE_A_NEW_BUILD/firebase.json \
  --non-interactive
```

Keep the project on Spark with billing disabled to satisfy the free-only scope.
Do not add Cloud Run/functions rewrites or deploy the full-cloud draft. Hosting's
published no-cost limits are 10 GB storage and 10 GB/month data transfer; without
Blaze, exhausted transfer quota can pause access until the next monthly period.
See [Firebase's Hosting quota documentation](https://firebase.google.com/docs/hosting/usage-quotas-pricing).
No promise of unlimited free traffic is made.

The current verified build is under ignored
`output/firebase_free_share/20261026-activity-v1`. Build reports record public
scope and sizes; they are not served.

## Verification

Working-curation release: 54 Node tests, 51 existing Python review tests and five
working-input/executor Python tests passed. Local static and published browser
checks cover real CH30 membership changes, exact Jacob/Agent matching, Phase 1 →
Phase 2, merge reinspection, discard, undo, result export, refresh recovery,
independent reviewers and explicit continuation, at 1024/1920/2560/390 widths.
The public checks observed zero JavaScript errors and zero non-GET network
requests. All five original MAT hashes remain unchanged; nine deployed frontend /
reference assets match the prepared build. Billing remained disabled at deploy.


51 Python regressions and 47 Node contract/static-adapter tests passed. Browser
checks passed locally and at the public URL for all six views, selected-pair ISI,
quality metrics, 156 default reasons, save/reload/export/restore, independent
visitors, and 1024/1920/2560/390 widths. No JavaScript errors, failed requests or
feedback network writes were observed in the static edition. Agent 3 was tested
against a local fake transport for both provider interfaces; no paid provider
requests were sent. The public site has no configured inference gateway. Precomputed metrics are also compared with the
original read-only calculation before final verification.

Earlier phase-contract release (superseded by the context-actions behavior above): removed Dataset notes and retains only the Viewing role
chip in evidence and matching right-panel cluster names. Phase 2 opens its recorded
pair, restricts new judgments to MERGE/NOT_MERGE/DISCARD, and hides Split tree.
Phase 1 uses KEEP/SPLIT/DISCARD. DEFER remains a separate status. Verified legacy
feedback is preserved and can still be restored. Browser checks cover both agents
and all 12 Phase 2 cases, corrected merge targets, draft/saved refresh and saved-view
editing. Right-side Viewing chips also follow a human merge target when displayed.

Saving or updating now stays on the current case and immediately shows the saved
User choice with an Edit button. The comparison-cluster choice is nested within
User choice, leaving only the Agent and User choice areas when the editor is closed.
The Save review / Update review labels no longer promise automatic advancement.
This frontend-only release reuses the previous verified public evidence snapshot
unchanged; it adds no model calls, backend services or published user feedback.

The responsive-rail release changes only frontend assets and reuses the verified
262-case snapshot unchanged. Typography follows viewport and panel width, and
the green navigation rail collapses to icons with keyboard support and a persisted
browser preference. Seven viewport sizes, both rail states, narrow panel dragging,
plot-label sizing and draft/reload recovery are checked locally and in the static
edition. Small screens start with the rail collapsed unless a preference exists.

The Agent card now starts expanded on reload, with the full action and retained rationale and no second rationale toggle. Hide/Show remains available. The original height grip
remains functional for explicit compaction; opening or resetting restores full
content. Rationale-exposure metadata reflects whether it was actually revealed.
The underlying dataset snapshot remains unchanged.
