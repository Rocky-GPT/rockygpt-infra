# RockyGPT release runbook

## Environment topology

Development happens on `dev` across repositories. Merges into `main` automatically deploy production services:
- **UI**: Vercel production deployment (`rockygpt.vercel.app`)
- **Brain**: Render Python service (`rockygpt-brain.onrender.com`)
- **Data**: Render Node.js service (`rockygpt-data.onrender.com`)

Free Render services sleep when idle. A first request may take roughly a minute; automation allows 90 seconds. Do not add synthetic keep-warm traffic.

## Promotion and Deployment

1. Develop and verify changes on each repository's `dev` branch.
2. Run local and CI test suites (`pytest`, `typecheck`, `lint`, and Playwright suites).
3. Merge `dev` into `main` in dependency order: **data, brain, UI**.
4. After production deployment, verify service health using the `Service Smoke` workflow or local runner. The scheduled production monitor repeats the check every six hours.

## API compatibility and SDK releases

OpenAPI is owned by the service repository. Additive changes increment its minor version. A breaking change requires a preserved `/v1` implementation, a new `/v2` path, and a major OpenAPI version. PR compatibility checks treat breaking errors as failures and report non-breaking warnings.

Tag an API release as `api-v1.1.0`; the tag version must exactly match `info.version`. The release workflow regenerates rather than commits clients, compiles TypeScript on Node 22, Swift on macOS, and Kotlin on Java 17, then attaches the spec and three SDK archives to the GitHub Release.

## Database changes

Use additive migrations first: add nullable/defaulted columns or new tables, deploy compatible readers, backfill, then remove obsolete fields in a later major release. Data and brain credentials must remain unable to access one another's schemas. Run the infra role-isolation check after changing grants.

## Secrets, incidents, rollback

Rotate secrets in downstream-first order: data credentials, brain-to-data token, then UI keys. During a two-value rotation, deploy accepting services before callers, verify, then revoke the old value. Never expose `ABUSE_HASH_KEY`, database URLs, or model keys to browser or native code.

For rollback, restore the last verified data release, roll brain back to the last compatible commit, then roll UI back. Do not roll an API owner behind a caller that requires a newer contract. On monitor failure, use its incident issue as the timeline, link deployments and smoke runs, and close it only after the recovery smoke passes.
