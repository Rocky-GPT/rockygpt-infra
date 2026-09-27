# 1Password production environment sync

The `Environment Sync` workflow copies explicitly managed values from 1Password
Environments to Render and Vercel. It runs every 30 minutes when the repository
variable `ENV_SYNC_ENABLED` is `true`. GitHub schedules can be delayed.

The setup is **disabled until the credentials, target flags, and first live run
are verified**. See `config/environment-sync.json` for the source Environment
IDs and explicit destination/key allowlists. Never put variable values in that
file. Preview, staging, local files, and unrelated hosting variables are outside
this production sync. Removing a source variable does not delete it remotely;
missing or empty managed values stop the run instead.

## Access

The official `onepassword-sdk` reads the existing Environments directly. MCP
continues to manage them interactively; unattended jobs use a service account.
There is no migration to a second set of vault items.

The service account has read-only access to these three Environments only:

- `RockyGPT - Production - Brain (Render)`
- `RockyGPT - Production - UI (Vercel)`
- `RockyGPT - Automation - Environment Sync`

It has no vault access and cannot create vaults. The automation Environment
stores `RENDER_API_KEY` and `VERCEL_TOKEN`. Vercel's token should be scoped to
the `rockygpt` project. Render's API key has broader account access. Save a
backup of the service-account token in the owner's private 1Password vault,
outside the Environments the service account can read.

Store that bootstrap token as `OP_SERVICE_ACCOUNT_TOKEN` in the GitHub
repository's `production` environment. Never paste it into a workflow, issue,
commit, or command argument. 1Password service-account Environment permissions
are immutable; adding another Environment requires a replacement service
account. Replacing its token also changes the keyed sync fingerprints, causing
one Vercel refresh.

## Activation

1. Populate and verify all intended 1Password source values. Vercel cannot reveal
   previously stored sensitive values; import originals or explicitly replace
   them. Split production variables shared with Preview into separate entries
   before enabling production sync. The script refuses to modify shared rows.
2. Create the scoped service account and hosting credentials, save them as
   described above, and enable only fully populated targets in the manifest.
3. Develop and commit on `dev`. Promote the reviewed changes to `main` without
   switching the local branch. GitHub runs scheduled workflows from its default
   branch (`main`), and this workflow rejects other refs.
4. Dispatch `Environment Sync` on `main` with **apply=false** and
   **bootstrap=true** for the initial Vercel plan. Confirm the managed key counts.
5. Dispatch with **apply=true**, **bootstrap=true**. Bootstrap permits the first
   write from reviewed 1Password data; it does not bypass source or scope checks.
6. Verify the deployment and readiness checks, then rerun with **apply=true**,
   **bootstrap=false**. It should report zero changed variables and not redeploy.
7. Set the repository variable `ENV_SYNC_ENABLED=true` to start the schedule.

The GitHub repository owner can monitor failures in Actions. The existing
production smoke monitor remains independent. Do not claim activation based
only on passing unit tests or creating credentials.

## Updating a secret

Create the replacement credential at its issuing service, update its value in
the appropriate 1Password Environment, and let the job copy it to hosting. The
sync does not create or rotate provider credentials itself. Make sure an old
credential remains valid until the new deployment has passed readiness.

Render values can be compared directly. Vercel sensitive values cannot be read
back, so its checkpoint combines a keyed fingerprint of the 1Password source
with each hosting variable's ID, update time, and type. A source change or a
hosting metadata change triggers an update. Values never appear in the
checkpoint or normal logs. Raw API/SDK errors are deliberately withheld.

Only changed targets redeploy. Render deploys the currently live Git commit;
Vercel rebuilds the currently live Git commit using the updated project
environment. The workflow checks for an existing deployment before writing and
does not intentionally deploy a newer application revision. External human or
Git-triggered deployments can still race the job; avoid changing deployment
configuration concurrently with a secret update.

## Checkpoints and failures

An `environment-sync-state` artifact records keyed fingerprints, hosting
metadata, deployment IDs, and pending flags. No plaintext variable value or
credential is written to the artifact. Each run restores the newest checkpoint
from this workflow on `main` and saves it even when a write or deployment fails.
The checkpoint is refreshed on successful unchanged runs and retained 90 days.

A partial variable write remains pending and is completed on the next run. An
accepted deployment is checked before any later writes. Failed deployments stop
retries for human review rather than causing a deployment loop. Inspect the
hosting logs and repair the source; a changed source permits a fresh attempt.
For a transient failure with unchanged values, clear only that target's failed
deployment checkpoint after confirming which deployment is live. Never clear a checkpoint
blindly: Vercel needs it to compare sensitive values. Missing/expired state,
interrupted runs without an artifact, and ambiguous API responses need review.
There is a small crash window between a remote change and uploading its local
checkpoint; this is not a transactional deployment system.

Set `ENV_SYNC_ENABLED=false` to stop scheduled writes. This does not revoke
credentials or affect running services. Revoke hosting/service-account tokens
separately if retiring the integration.

## Validation

```sh
python3 -m unittest discover -s tests -p test_environment_sync.py
python3 scripts/sync-environments.py --help
```

Unit tests simulate hosting services and cover dry runs, missing values,
unchanged runs, partial writes, failed/completed deployments, fingerprints,
Render pagination, Vercel bootstrap, and Preview scope isolation.

Official references: [1Password Environment SDK](https://www.1password.dev/sdks/environments),
[service accounts](https://www.1password.dev/service-accounts/get-started),
[Render API](https://render.com/docs/api), and
[Vercel API](https://vercel.com/docs/rest-api).
