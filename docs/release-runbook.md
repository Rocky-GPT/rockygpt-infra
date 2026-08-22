# RockyGPT release runbook

## Environment topology

`staging` is a long-lived promotion branch in UI, brain, and data. Render runs
the cold free `rockygpt-data-staging` and `rockygpt-brain-staging` services from
that branch. Vercel uses the UI staging branch as the stable preview. Staging
has its own Neon project, roles, service token, budgeted OpenAI key, and
read-only artifact credentials. It must never receive production chat or
feedback records.

Free Render services sleep when idle. A first request may take roughly a minute;
automation allows 90 seconds. Do not add synthetic keep-warm traffic.

## Promotion

1. Merge feature PRs into each repository's `staging` branch.
2. Deploy in dependency order: **data, brain, UI**.
3. Run the infra `Service Smoke` workflow with the `staging` environment. It
   verifies readiness, direct and proxied map data, malformed chat handling,
   and denial without the staging service token.
4. For a significant answer, prompt, model, or campus-data change, manually run
   the complete `rockygpt-evals` suite against staging. This is the paid-model
   promotion gate; record its workflow URL on the promotion PR.
5. Open staging-to-main PRs in the same dependency order. Runtime changes to
   main should originate from staging. Merge only after deterministic CI,
   compatibility, SDK compilation, smoke, and required conversation resolution.
6. After each production deployment, run the production smoke. The scheduled
   monitor repeats it every six hours.

Main and staging should require pull requests, CI and contract checks, resolved
conversations, and block force-push/deletion. Zero human approvals are required
for a solo-maintainer repository. Administrative bypass is for incident recovery
only and must be followed by a normalizing PR.

## API compatibility and SDK releases

OpenAPI is owned by the service repository. Additive changes increment its
minor version. A breaking change requires a preserved `/v1` implementation, a
new `/v2` path, and a major OpenAPI version. PR compatibility checks treat
breaking errors as failures and report non-breaking warnings.

Tag an API release as `api-v1.1.0`; the tag version must exactly match
`info.version`. The release workflow regenerates rather than commits clients,
compiles TypeScript on Node 22, Swift on macOS, and Kotlin on Java 17, then
attaches the spec and three SDK archives to the GitHub Release.

## Database changes and data synchronization

Use additive migrations first: add nullable/defaulted columns or new tables,
deploy compatible readers, backfill, then remove obsolete fields in a later
major release. Data and brain credentials must remain unable to access one
another's schemas. Run the infra role-isolation check after changing grants.

The weekly/manual `Staging Data Sync` restores only a verified campus artifact
into the independent staging database. Brain tables start empty. Never clone or
restore production chat tables into staging.

## Secrets, incidents, rollback

Keep production and staging values distinct. Rotate in downstream-first order:
data credentials, brain-to-data/service token, then UI token. During a two-value
rotation, deploy accepting services before callers, verify, then revoke the old
value. Never expose `STAGING_SERVICE_TOKEN`, `ABUSE_HASH_KEY`, database URLs, or
model keys to browser or native code.

For rollback, restore the last verified data release, roll brain back to the
last compatible commit, then roll UI back. Do not roll an API owner behind a
caller that requires a newer contract. On monitor failure, use its incident
issue as the timeline, link deployments and smoke runs, and close it only after
the recovery smoke passes.
