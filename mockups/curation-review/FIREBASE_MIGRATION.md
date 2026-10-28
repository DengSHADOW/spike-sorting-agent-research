# Firebase migration status — 2026-10-23

**Current decision:** full migration is paused. The user instead authorized the
free Spark Hosting edition, published at https://spikeagent-review-20261023.web.app/.
See [the active free edition](FIREBASE_FREE_SHARE.md). The full deployment proposal
below remains historical preparation; Cloud Run, Firestore, Auth and private
Cloud Storage are not used by the public static edition.

This is a preparation record, not a deployment-complete report. The local service
continues at `http://localhost:8765` through the existing SSH port forward.

## Created and verified

- Firebase CLI authenticated as the project owner; credentials remain in the CLI's
  user configuration outside the repository. No authorization code is stored here.
- Independent Firebase project: `spikeagent-review-20261023` (number `681924719345`).
- Project name: **SpikeAgent Review**; Firebase state: `ACTIVE`.
- Web application: `1:681924719345:web:0c675f34199e368081af50`.
- [Project console](https://console.firebase.google.com/project/spikeagent-review-20261023/overview).
- Billing inspection: `billingEnabled=false`; the signed-in account has no visible
  Cloud Billing accounts. No billing account was linked and no Cloud Run service,
  build, image repository, database or data bucket was provisioned.
- No website release has been deployed. The project ID is reserved; a working
  public review URL has not yet been verified.

## Private data snapshot

`backend/prepare_firebase.py` prepares a new ignored local directory. It performs
no network requests and cannot deploy or enable billing. Run from the repository
root with the existing interface environment and a **new** destination:

```sh
.venv-interface/bin/python -B mockups/curation-review/backend/prepare_firebase.py \
  --output output/firebase_migration/CHOOSE_A_NEW_NAME
```

Verified snapshot: `output/firebase_migration/20261023-prebilling/`.

| Item | Verified value |
| --- | --- |
| Files, including generated scoped metadata | 427 |
| Total bytes | 320,804,247 (305.94 MiB) |
| Imported views | 6 |
| Historical/manual review cases | 166 |
| Jacob default reasons | 156 |
| Local saved reviews / events at snapshot | 0 / 0 |
| Uploaded to cloud | No |

The snapshot contains the four approved development MAT inputs, declared bundle
catalogs/images/evidence, only the checkpoints needed by those cases and their
metadata, matching saved-run manifests, scope configuration and the verified
reason seed. Files are copied into `data/` with repository-relative paths and
checked against an inventory of SHA-256 hashes. Original files remain unchanged.
The MAT manifest is filtered to those four datasets. The generated portable
registry is metadata for the future cloud adapter; it does not replace the live
local registry and is not directly a local server installation.

Credentials, Git metadata, personal uploads, the annotation workbook, model
weights, unapproved datasets, full experiment directories and SQLite files are
excluded. The raw approved MATs may contain expert fields; they must remain in a
**private** bucket. The review analysis must continue to read only its existing
allowlisted input fields, with no browser route serving raw MATs.

Feedback ownership must be mapped to authenticated Firebase UIDs before migrating
any existing review records. Recheck the live database immediately before cutover;
this snapshot is not a feedback backup and new local reviews can arrive afterward.

## Proposed deployment to review before billing

| Component | Concrete scope |
| --- | --- |
| Firebase Hosting | Existing static review frontend; same-origin `/api/**` rewrite |
| Cloud Run | Python review API and CPU analysis; service `spikeagent-api`, `us-central1`, proposed 1 CPU / 2 GiB, minimum 0, maximum 1 instance, concurrency 4 |
| Firebase Authentication | Google login; server verifies sessions and derives reviewer ownership from UID; owner manages reviewer access |
| Firestore | Persistent reviews, revisions, idempotency/event history, profiles and shared reasons; client direct access denied |
| Private Cloud Storage | Hash-verified evidence, approved inputs/checkpoints and bounded upload staging |
| Cloud Build / Artifact Registry | Build and retain the API container; exclude private data and credentials from the build context |

These are proposed settings, not resources already running. Cloud Run's local
filesystem must be treated as a cache; the current SQLite database is not suitable
as the durable cloud store. The local server's anonymous `X-Reviewer-ID` header
cannot establish ownership on a public service. Do not expose it unchanged.

The largest approved input is about 156 MB. Preserve upload functionality using a
private resumable storage upload and server-side manifest/hash validation rather
than sending that file through the Hosting/API request path. Background processing
must have a durable job state and a supported execution lifetime before it is
advertised as available. Existing imports should continue to reuse verified views.

No model inference, GPU allocation, experiments or training are part of this
website deployment.

## Billing prerequisite and remaining work

Firebase Hosting with Cloud Run requires a linked Cloud Billing account even when
usage falls within a free allowance. Linking it upgrades the Firebase project to
Blaze. Billing is usage-based; no fixed monthly charge or zero-cost guarantee is
asserted. Cloud Run, storage/egress, database operations, image storage and builds
are the relevant potential charges. See [Firebase's Cloud Run setup guide](https://firebase.google.com/docs/hosting/cloud-run)
and [Firebase pricing](https://firebase.google.com/pricing).

The owner needs to create/link an account using the project's
[billing page](https://console.cloud.google.com/billing/linkedaccount?project=spikeagent-review-20261023).
Payment details must be entered only in Google's console. A proposed **US$5/month
alert budget** is a monitoring threshold, not a guaranteed spending cap; budget
alerts do not automatically stop this service. Review
[Google's budget documentation](https://docs.cloud.google.com/billing/docs/how-to/budgets).

Still required: cloud API/authentication adapter, Firestore persistence with
equivalent transaction/revision behavior, private object delivery, durable import
handling, Hosting rewrite/container/IAM configuration, offline regression tests,
then authorized provisioning and deployment. Verify login/access denial,
cross-reviewer isolation, save/reload/undo/export/restore, reason persistence,
all six dataset views, pair statistics and uploads against the public URL before
calling the migration complete. Keep the local service available until that passes.
