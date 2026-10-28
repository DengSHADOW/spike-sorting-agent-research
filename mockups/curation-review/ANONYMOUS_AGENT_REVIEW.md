# Anonymous agent review — 2026-10-26

**Working-curation scope update:** `/curation.html` now executes edits on separate
browser working copies, with immediate single/pair context actions, merge reinspection and exact-state
Jacob references. This document's snapshot/annotation limitations below apply to
the original Review page. Working copies do not request fresh Agent responses;
a changed state with no matching archive shows no suggestion. See the
[working workflow and dataset import guide](README.md#working-curation-update--2026-10-26).

The latest interface defaults Agent to **open**, with the full retained rationale.
Current clusters is the primary working flow; optional Saved snapshots retains
past states without mixing them into current tasks. Compare with controls the
available single/pair actions immediately. Original phase and Agent pair remain
immutable provenance; human judgment_context / judgment_pair record the chosen
object. Frozen experimental controller behavior is unchanged.

The reviewer interface now compares **Agent 1** and **Agent 2** using 90 matched
historical CH30 reference states. Model brands, source run paths and response
archive filenames are absent from the normal UI and the published study bundles.
The current website is https://spikeagent-review-20261023.web.app/.

An optional **Agent 3 live-model window** is disabled by default. Open **Data &
inputs → Research options · live closed-source agent**, then enable it. Agent
selection is inside the right yellow suggestion box. Agent 3 opens a separate
window with **OpenAI-compatible / OpenRouter** and **Anthropic Claude** interfaces;
it does not load the archived third-model predictions.

The live window requires a separately configured HTTPS inference gateway, its
access token, and a server-configured model allowlist. This release has no
configured public gateway and makes no paid request. Provider keys stay on the
server. Connection checks do not call a model. An enabled gateway still requires
an explicit confirmation for each request. Preview responses can be exported;
they do not overwrite expert feedback, run curation or become controlled
experimental results.

## Comparison behavior

- Changing the agent keeps the same `comparison_case_id`, recording, current
  cluster, phase and state hash. Responses have different case IDs, so one
  agent's review cannot overwrite another's. Unsaved drafts require an explicit
  discard choice before switching.
- Agent 3 can be shown/hidden without switching the current Agent 1 / Agent 2
  case or losing its draft. Archived third-model data remain private for audit.
- The input inspector also uses anonymous IDs. Real model identities are kept in
  ignored `output/curation_review_local/anonymous_agents_20261026_v1/private_agent_mapping.json`.
  That file is never copied to Firebase or included in feedback exports.
- The action-only archive has **no retained rationale**. Its UI says so, changes
  the agreement prompt to action-only and disables rationale disagreements.
  No explanation is synthesized. Feedback records `rationale_available=false`.
- Data & inputs shows each reviewer's per-agent counts: agreement, disagreement
  and defer. These are human judgments, not ground-truth model accuracy.
- There are five initial-cluster manual views. Older 9B bundles and
  feedback remain on disk for audit/backup, but are removed from the active
  selector and the new public release.

## Compact reviewer labels

Queue cards show the source cluster (or selected comparison), a smaller, muted
step number, and a saved-review status badge;
file grouping and filters remain. Historical-protocol/state-matching details remain in research options and the
input inspector; the repeated paragraph is removed from the agent box. The recorded action has its own prominent border
and uppercase label, with source/target names on a separate line.

One-cluster / Two-cluster decision describes the number of clusters involved
in the saved decision, not the number of neurons or cluster purity. A matched
state means both agents refer to the same pre-action assignment state; it is
not agreement or an expert validation label. The old redundant matched-case
badge is removed. Only the Viewing role label is shown, with an explanatory tooltip. It follows
the cluster(s) currently displayed in the evidence. The same label appears next
to corresponding cluster names in the right Agent and User choice cards. It is
not a completed-review mark; the recorded input roles remain in provenance.

## Integrated cluster detail and panel layout

Queue entries show a Reviewed badge only after a judgment is saved; saved DEFER
judgments show Deferred. Undo removes the badge. These labels refer to the case,
reviewer and agent, not to a newly opened comparison preview.

Cluster detail has a Compare with selector, initially empty for Phase 1 and
preselected to the original recorded pair target for Phase 2. Its candidates are the other active clusters in the same
recording and checkpoint. Selection keeps the source fixed, shows shared-axis
source/target waveforms and candidate ISI, and updates the title, queue entry,
role labels and compact header counts to `Cluster A vs. Cluster B`. Clearing the
selector returns Phase 1 to a single-cluster view. Phase 2 always retains a pair. There is no separate Compare pair
tab. Timing and Quality metrics use the selected pair; amplitude-over-time shows
both named clusters. Original saved images retain explicit provenance.

Selecting a comparison never rewrites the recorded agent source/pair/answer or
saves a judgment by itself. Agreement still reviews that recorded suggestion.
An explicit human MERGE proposal is linked to the selected candidate; both target
selectors stay synchronized, and clearing the comparison clears that proposal's
target. The saved v3 exposure includes the actual viewed pair in
`pair_comparisons`, plus the legacy `pair` exposure marker. Case IDs and original
AI pairs remain unchanged. Anonymous agent switching preserves a valid selected
candidate without changing a restored human judgment.

On desktop screens (at least 1000 CSS pixels wide), two focusable drag separators
resize the three independently scrollable panels. Arrow keys adjust width,
Shift increases the adjustment, and double-click/Home/Enter resets the widths.
Widths are remembered in this browser and clamped to keep evidence usable.
Smaller screens stack the panels and hide the separators. Chart zoom/pan bounds
and typography are refreshed during resizing.

## Pinned controls and compact evidence labels

The queue filter block and the center title/statistics/tab block stay pinned
while their panels scroll; the original panel scrollbar placement and styling
remain. The case counter above the title displays the current catalog position
(e.g. 2 / 90). The wider statistics card has one complete row per selected
cluster, stacked source first and candidate second. Cluster selector options
contain names only; spike counts stay in the statistics and relevant evidence.
Plot keys identify clusters and trace meanings without Solid/Faint prefixes.

Dataset notes is removed. The right
panel is titled Agent and your review; its repeated review-object line and
matched-state/protocol paragraph are removed. Protocol/provenance information
still exists in the input inspector, research options and feedback metadata.

Reason suggestions and full-library cards show source cluster IDs at the lower
right, next to provenance. Jacob IDs are parsed from the audited original action
references (discard sentinel zero excluded). Reuse in a current review never
relabels a Jacob reason's original source. Reviewer-added reasons derive their
cluster from matching saved reviews; unknown provenance is explicitly labeled
Cluster not recorded. Confidence, sufficiency and missing-evidence options have
icons and text explanations; underlying stored values remain unchanged.

## All-file tree and persistent choice cards

Input file selection is now in the queue; the duplicate top-bar selector is
removed. All files shows expandable folders for every approved recording plus
the separate synthetic demo. A recording with both matched-agent and manual
initial-cluster views has named subgroups. Folder counts show pending/saved review
entries for the current agent/view, not unique neuron counts: the same cluster
at different steps can be separate entries. File/phase/search/status filters and
unsaved-draft protection apply across the tree. Each file summary shows its own progress bar; the global footer is removed.

Cluster leaves show action chips immediately after a choice (Draft), and keep
the saved action chip after save/reload. The Agent panel starts expanded on every page load. Its header stays
pinned; Hide/Show controls the full card in the right panel's normal scroll flow. The repeated
Agent label and Recorded agent action caption are removed.

User choice displays action, merge target, reason, note/comment and selected
confidence/evidence/missing-evidence tags. Drafts are distinguished from saved
judgments, and opening a saved case restores its card and editor values. The
comparison card looks for a saved source-cluster review by this reviewer in the
same dataset and agent. Exact checkpoint matches take priority. A different
checkpoint is explicitly labeled Other state; appearing only as another review's
merge target does not count as that cluster's source review. No record yields a
short no-saved-choice message. Records from other agents/reviewers are isolated.
Existing v3 save/undo/export/restore semantics and spike assignments are unchanged.

## Refresh recovery and phase scope

The model-input inspector is in the top bar. The redundant run-summary row and
cluster-leaf Step captions are removed; the case counter includes its channel.
Only Viewing is shown for the currently viewed cluster(s), including matching
cluster names in the right panel. Agent input and Review source chips are removed.

Committed reviews continue to use the existing local SQLite or browser-local
static adapter. In-progress editor values now also have browser-local draft
recovery, keyed by reviewer and bound to case, recording, checkpoint, run, agent,
base revision and saved timestamp. A refresh restores the selected case and draft
without creating a saved judgment or advancing revisions. Explicit save removes
the draft after a confirmed write; explicit discard removes it while keeping any
previously committed judgment. A draft whose basis no longer matches a newer
saved review is not applied automatically. Storage failure shows the existing
warning. Source selection (including the separate demo) is remembered as well.

The public site's storage remains specific to the same browser/origin/reviewer;
it is not multi-computer cloud synchronization. Clearing browser storage removes
that local data. JSON export/restore remains the transfer/backup mechanism.

The pipeline's Phase 1 classifies one source cluster with KEEP/SPLIT/DISCARD.
Phase 2 evaluates a source/candidate pair with MERGE/NOT_MERGE/DISCARD. The current
matched CH30 catalog has 84 Phase 1 and 6 Phase 2 reference cases per agent. The
website can inspect and annotate both phases, including comparison measurements,
refresh and undo. New judgments are restricted to their phase in the editor,
Core validator, browser save adapter and local backend. DEFER is a separate review
status. Verified legacy exports can still restore old cross-phase proposals with
their phase_contract_compatible=false flag; editing them requires a current valid
phase action. Keep separate judges the original recorded pair: selecting it
returns the comparison to that pair. A different candidate can be inspected and
proposed as a corrected MERGE target without rewriting the recorded AI pair.
The Snapshots page records annotations without executing actions; Current clusters executes working-copy edits. Neither page trains a model.

Split tree shows an available saved Phase 1 split hierarchy, rather than executing
a split or proving cluster quality. Its tab is hidden in Phase 2 and for real
cases without a saved tree image. Current reconstructed anonymous evidence has
no original tree PNGs; the interface does not fabricate them.

## Historical snapshots and saved-view editing

The CH30 reference list contains 34 source-cluster-1 cases at 34 distinct verified
checkpoints. Their member counts range from 332,472 (case 6) to 5,049 (case 69).
These are repeated evaluations of a changing cluster ID along the fixed reference
trajectory, not duplicated imports or proof that the Agent's proposals were
executed. Repeated IDs are now folded under one cluster heading, with independently
selectable spike-count snapshots; case IDs and annotations remain separate.

Agent decisions contains archived model suggestions for each reference state.
Initial clusters · manual contains one case per initial MAT cluster, with no
recorded Agent action. File progress counts review entries across the selected
agent/manual views, not unique neurons. Progress bars are part of each file's
summary and remain visible when it is collapsed. The old global progress footer
and per-file Pending/Reviewed text labels are removed.

The Agent body retains its vertical drag separator and keyboard height adjustment.
Each Show defaults to the full natural content height, including retained rationale;
there is no separate rationale Show/Hide button. Explicit height resizing can
compact the card with an internal scrollbar; double-click/Home/Enter restores
full content. The expanded card uses the outer right-panel scroll so long text
cannot pin over the editor. A missing-rationale notice is retained when needed.
Rationale exposure is recorded only when it is shown, once per opened case.
The top inspector button now has a visible label.

User choice is at the end of the review area. Committed cases start in a read-only
view with an Edit button; editing expands the existing form. Save returns to the
read-only view and preserves revision/audit semantics. Cancel edit discards only
the unsubmitted draft. A restored in-progress edit reopens the editor as Draft.
A comparison while read-only cannot change a saved merge target or create a draft;
Ctrl/Cmd+Enter cannot resubmit a read-only saved judgment. Comparison-cluster choices are nested inside the User choice card, so a saved
case has only the Agent and User choice areas on the right. Successful save/update
stays on the current case and immediately closes the editor; it does not advance
to another pending case or open History automatically.

## Interpretation and data boundary

The two active runs share the same 90 reference pre-action states. Their historical
prompts, image rendering, token budgets and response protocols differ. This
interface supports anonymous human review of those records; it does **not**
establish a controlled model-performance comparison or an effect of model size.
The input inspector and research options disclose that limitation.

Human reference actions are replayed **offline in memory** to reconstruct the
states, with every before/after assignment hash checked against the archived
reference. Original MATs, annotations and experiments are unchanged. The new
bundles do not contain matched expert answers or expert terminal labels.
Waveforms/metrics are reconstructed **reviewer evidence**, not claimed to be
verified original images sent to every historical model. The footer, inspector
and export preserve this distinction.

New canonical feedback fields are `study_id`, `study_agent_id`,
`comparison_case_id`, `rationale_available`, `response_sha256`, `evidence_scope`
and `comparison_protocol_note`. They accompany the existing anonymous `agent_id`,
state/input hashes, action, rationale and expert correction. Backend reconstruction
uses the stored case metadata, ignoring client-provided identity claims. The
existing v3 schema, revision/idempotency handling, undo, export and restore remain.

## Operations

The offline importer is `backend/import_agent_study.py`. It creates new packages
under `output/curation_review_local`; it imports no provider or model runner.
The prior mutable catalog was backed up as
`manager/registry.before_anonymous_agents_20261026.json` before adding the study.

The free Hosting builder publishes 7 bundles: 2 agents × 90 matched cases plus
82 manual cases across five approved review datasets, **262 review records** total.
Archived Agent 3 cases are excluded from the public manifest and bundles.
Raw MAT/NPZ, SQLite, private mappings and credentials are excluded. Hosting stays
on the existing free Spark scope, with browser-local feedback and no Cloud Run,
functions or database.

## Reviewer and history behavior

The lower-left Reviewer button opens a name/study-ID input and a saved-profile
selector. Creating a profile creates a new immutable reviewer key and separate
history; renaming the current profile keeps its key and existing records.
Names are not authentication. Public-site histories are browser-local; the
Runpod edition stores real reviews on its existing SQLite server. Export/restore
JSON when moving between computers.

**History** displays the latest saved judgment per case in the active dataset
and agent. Undo removes that current judgment. **Revisions** is now an actual
review audit log, showing saves, edits, restores and undo events, their real
revision numbers and changed fields. Draft typing and opening charts do not
create saved revisions. It does not train a model or revise the curation policy.

## Optional gateway configuration

`backend/closed_gateway.py` exposes only authenticated `/api/closed/status` and
`/api/closed/request`; it is separate from the private review server. It is not
started or published by Firebase Hosting. HTTPS/tunnel setup is still required
for use from the public site. Dedicated environment configuration:

- `SPIKE_CLOSED_GATEWAY_TOKEN`: randomly generated secret, at least 32 characters.
- `SPIKE_CLOSED_ALLOWED_ORIGINS`: comma-separated exact web origins.
- `SPIKE_CLOSED_OPENAI_API_KEY`, `SPIKE_CLOSED_OPENAI_MODELS` (comma-separated).
- `SPIKE_CLOSED_OPENAI_ENDPOINT`: defaults to the official OpenAI Chat Completions
  endpoint; `https://openrouter.ai/api/v1/chat/completions` also supported.
- `SPIKE_CLOSED_ANTHROPIC_API_KEY`, `SPIKE_CLOSED_ANTHROPIC_MODELS`.
- `SPIKE_CLOSED_ENABLE_REQUESTS`: defaults off; only `1` enables requests.
- `SPIKE_CLOSED_MAX_OUTPUT_TOKENS`: 64–4096, defaults to 1024.
- `SPIKE_CLOSED_MAX_REQUESTS`: 1–100, defaults to 10 total submitted requests
  in the gateway ledger, including failed/ambiguous requests.

Never put provider keys or gateway tokens in the static bundle or Git. The UI
holds the gateway token in memory and clears it when the window closes.
Successful responses are idempotent per request ID; failures are never retried
automatically. An ambiguous timeout can still incur a provider charge.

A preview sends up to three currently displayed PNG charts with their evidence
titles/legends and canonical source/target/state metadata. It excludes existing
AI answers, expert corrections and reason libraries. This reviewer snapshot is
explicitly different from the frozen experiment protocol. Both provider schemas
were checked against official documentation; tests inject fake transports and
never call a provider.

Tests cover anonymous filtering, profiles, separate per-agent feedback, real
audit diffs, both API payloads, gateway origin/token checks, request caps,
idempotency and failure handling. Browser checks cover layout, save/edit/undo,
reviewer switching, reload, hidden live configuration and mocked live previews.

## Meaning of repeated cluster IDs and initial manual records

The case identity includes recording, state/checkpoint, phase, source/pair and
agent; cluster number alone is not a stable membership identity. For CH30, reference
step 6 splits cluster 1 (332,472 spikes) into retained cluster 1 (331,733) and
breakaway cluster 239 (739). Case 8 inspects that smaller cluster 1 again.
These are historical reference changes, not effects of website annotations.
Historical snapshots should remain available; reviewers can use a predefined
subset if the study does not require all 90 judgments. Multiple snapshots are
not independent neuron samples. Another person should use a separate Reviewer
identity on the same snapshot; Edit under an existing identity is a revision.

Initial clusters · manual is an initial-MAT catalog with ai=null, not an inference
run. Its CH30 state equals the first matched-agent state, but that first Agent
case addresses only cluster 211, not all 18 initial clusters. Existing full
rollouts remain valid archived experiments; this UI has not integrated the complete
rollout catalogs for the selected agents. The current anonymous 90-case comparison
uses saved stepwise predictions on expert reference states. A rollout view must
bind each suggestion to its actual run, pre-action state and source/target; neither
step number nor cluster ID alone safely transfers suggestions to the initial view.

## Default cluster queue and authorized CH17 import

The default Review view is Initial clusters: one task per initial cluster
and 82 tasks across five channels. Historical agent cases is an explicit optional
view with 90 records per selected agent, separate progress and retained state groups.
A historical judgment never completes or overwrites a manual cluster task. Saved
view selection and all existing feedback remain available after refresh.

CH17 (`cM2-e004_011-015_CH17`) was authorized for interface-only manual review on
2026-10-26. Its source hash and six actor fields validate: 87,411 spikes, 45 waveform
samples, 30 kHz, 10 initial clusters. It is in the existing CH20 development block,
not an independent new recording or unexposed test set. Original data, experimental
protocols and protected blocks are unchanged; no new inference was performed.
The current feedback collector does not execute reviewed operations or provide
a shared continuation workspace. See the root project document for that boundary.
