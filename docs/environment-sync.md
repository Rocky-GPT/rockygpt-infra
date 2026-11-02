# 1Password production environment sync

The `Environment Sync` workflow selects secrets from the RockyGPT 1Password Environment
with public settings in `config/environment-sync.json`, then updates Render,
Vercel and GitHub Actions secrets. It runs every 30 minutes when the repository
variable `ENV_SYNC_ENABLED` is `true`. GitHub schedules can be delayed.

Scheduled writes require **verified credentials, enabled target flags, and a
successful first live run before setting `ENV_SYNC_ENABLED=true`**. See
`config/environment-sync.json` for the source Environment
IDs and explicit destination/key allowlists. Each entry in `sources` maps an
Environment ID to its required `keys`. A `keys` object maps destination names
to stored names (for example, `DATABASE_URL` to `PRODUCTION_DATABASE_URL`);
legacy identity lists are also supported. Duplicate destination assignments are rejected. `settings`
contains approved public configuration such as the Brain URL, environment name,
and OpenAI project ID. Never put a credential in that file.
Preview, staging, local files, and unrelated hosting variables are outside
this production sync. Removing a source variable does not delete it remotely
(list it under `retired` for that); missing or empty managed values stop the run instead.

## One manifest for production and the laptop

The laptop launcher (`rockygpt-workspace/local-env.py`) reads the `settings` of
`brain-production` and `ui-production` from this same file, overriding only
`BRAIN_ENVIRONMENT=development`. So a public setting is added in one place and
reaches Render on the next sync and the laptop on its next start. Put a new setting
on `dev` first to try it locally; it reaches production when `dev` is released to
`main`. Add its name to `ALLOWED_SETTINGS` in `scripts/sync-environments.py`.

Every run also lists, by name only, the variables a target holds that the manifest
does not manage (set by hand in a dashboard, or left over from an old setting). They
appear as a warning on the workflow run, because the laptop never sees them. Either
manage the variable here or, if it is deliberately kept by hand, add its name to the
target's `manual` list.

To clear a variable nothing reads any more, take it out of `sources`/`settings` and add
its name to the target's `retired` list. The next applied run deletes it from that
target (and, for Render and Vercel, redeploys the live code so the old value is gone
from the running service). Only names in `retired` are ever deleted; a retired name
cannot also be managed or listed as `manual`. Leave the name in `retired` afterwards:
it then stops a hand-typed copy from coming back unnoticed.

## GitHub Actions secrets

`provider: "github"` targets keep a repository's Actions secrets (or one deployment
environment's, with `environment`) equal to 1Password. This is what would have caught
the data repository's `OPENAI_API_KEY` sitting dead from August: a key replaced in
1Password now reaches every workflow that uses it. Workflows read the new value on
their next run; nothing redeploys. GitHub secrets are write-only, so as with Vercel
the checkpoint pairs a keyed fingerprint of the source with each secret's update time,
and an edit made directly on GitHub is overwritten by the next run. The sync never
manages its own `OP_SERVICE_ACCOUNT_TOKEN`.

The GitHub targets ship disabled. To turn them on:

1. Create a fine-grained GitHub token with resource owner **Rocky-GPT**, access to
   `rockygpt-data`, `rockygpt-infra` and `rockygpt-evals`, and repository permissions
   **Secrets: Read and write** and **Environments: Read and write**.
2. Save it in the RockyGPT 1Password Environment as `GITHUB_SECRETS_TOKEN`.
3. Check each target's mapping (for example, data's `OPENAI_API_KEY` comes from
   `BRAIN_OPENAI_API_KEY`), set `enabled: true`, and release to `main`.
4. Dispatch with **apply=false**, **bootstrap=true**, read the counts and any
   unmanaged-name warnings, then dispatch with **apply=true**, **bootstrap=true** once.

## Access

The official `onepassword-sdk` reads the existing Environments directly. MCP
continues to manage them interactively; unattended jobs use a service account.
There is no migration to a second set of vault items.

All runtime mappings use the **RockyGPT** Environment (ID
`q67qwjjknjx6nqrbzdus6sa3vi`, formerly Shared). Reusing its ID preserves the
existing service account's read access. The account can read the consolidated
Environment, including local and storage secrets; the sync's explicit allowlists
select only the hosting tokens and each production service's assigned values.
It has no vault access and cannot create vaults.

`RENDER_API_KEY`, `VERCEL_TOKEN` and `GITHUB_SECRETS_TOKEN` live in the same Environment. Vercel's token
should be scoped to the `rockygpt` project. Save a backup of the service-account
token in the owner's private 1Password vault, outside the Environment it reads.

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

The RockyGPT Environment stores shared API keys once. Local and production
databases use `LOCAL_DATABASE_URL`, `LOCAL_LEDGER_DATABASE_URL`,
`PRODUCTION_DATABASE_URL`, and `PRODUCTION_LEDGER_DATABASE_URL`.
`PRODUCTION_SERVICE_TOKEN` maps to the application's `SERVICE_TOKEN`
(formerly `STAGING_SERVICE_TOKEN`, now retired, so the sync deletes it). UI
hashing, artifact storage and hosting API keys
retain their service-specific names.

The sync reads the Environment once and maps only each consumer's assigned
keys. Local database and artifact credentials are never sent to Render or
Vercel. Public settings remain in the manifest and are promoted to `main`;
they do not need duplicate entries in 1Password.

Create the replacement credential at its issuing service, update its value in
the RockyGPT 1Password Environment, and let the job copy it to hosting. The
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
