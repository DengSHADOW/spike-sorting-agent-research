# SpikeAgent expert review — local backend and meeting demo

The buildless frontend supports multiple imported Jacob datasets, saved Qwen9B decisions,
initial-cluster manual review without AI suggestions, and the separate six-case synthetic demo.

Real-data reviews are validated and persisted in SQLite on this Runpod workspace. The demo retains
its original browser-local v2 storage/export. Both sources include review, history and a conceptual
revision view. No model API, learning job or curation executor is connected; proposed actions are annotations.

## Local backend (2026-10-23)

Use the project-specific Python 3.12 environment; do not modify `/workspace/.venv`:

```sh
uv venv --python /usr/bin/python3.12 .venv-interface
uv pip install --python .venv-interface/bin/python -r mockups/curation-review/backend/requirements.txt
.venv-interface/bin/python -B mockups/curation-review/backend/import_jacob.py
.venv-interface/bin/python -B mockups/curation-review/backend/server.py --port 8765
```

The importer is a one-time offline operation. Its default output must not already exist; use a **new**
`--output` child of `output/curation_review_local` for a fresh import and supply it to `server.py --bundle`.
Do not rerun against or delete the existing bundle to restart the website. The server itself needs only
the Python standard library. It binds to `127.0.0.1`; forward port 8765 in Remote SSH/VS Code, then visit
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

Cluster detail shows the original Phase 1 four-PNG / Phase 2 three-PNG evidence, including actual ISI
histograms and amplitude distributions. Overview, Gallery, comparison-cluster detail and amplitude-over-time
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
The four approved development channels are CH31, CH3, CH20 and CH30; reserved/quarantined inputs
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
- Server persistence for the name in the Reviewer ID header. Copy/use the anonymous reviewer key to continue
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
library ingestion, additional dataset schemas outside the four approved channels, and feedback-driven skill learning.

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
   The library starts empty: no options are claimed to come from Jacob's annotations.
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
Jacob-derived annotation options, live model integration, executing expert operations and skill learning
remain separate integration work.

Questions for the meeting:

- Which evidence should the expert see first?
- What should require an explicit correction reason?
- Should labels and operations be separate controls?
- What confirmation should a real discard, merge, or split require?
- Which feedback should drive the next experiment?

The site is private by default. A local copy can be screen-shared during the meeting.
