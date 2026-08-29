# RockyGPT release runbook

## Environment topology

Development happens on `dev` across repositories. Merges into `main` automatically deploy production services:
- **UI**: Vercel production deployment (`rockygpt.vercel.app`)
- **Brain**: Render Python service (`rockygpt-brain.onrender.com`) — serves campus data as well as answers
- **Data**: retired 2026-08-28. `rockygpt-data` no longer exists on Render; the repository is kept for its ingestion tooling only.

Free Render services sleep after 15 minutes idle, and a first request then takes roughly a minute; automation still allows 90 seconds for anything that may hit a cold instance.

The brain is deliberately kept awake, by **two** things — check both before reasoning about cold starts or instance hours:

- `rockygpt-brain/.github/workflows/chat-log-persistence.yml`, every 15 minutes. Its purpose is alerting on the chat-log store, but the cadence sits exactly on Render's idle threshold, so warming is a documented side effect. GitHub cron is best-effort and drifts late, so it warms unreliably.
- An UptimeRobot monitor (`RockyGPT brain (keep-warm)`), `GET /health` every 5 minutes. Added 2026-08-28 for a dependable cadence and external alerting.

Together these reverse the previous rule against synthetic keep-warm traffic. They are affordable only within a budget worth stating explicitly:

- Render grants **750 free instance hours per calendar month per workspace**, not per service. Exhausting the pool suspends *every* free service until the 1st.
- Hours accrue only while a service is awake. Ping frequency is irrelevant to cost — a 5-minute and a 14-minute interval bill identically; only hours-awake matter.
- The brain awake continuously costs 744 h in a 31-day month, against a 750 h pool. The margin is under 1%, so a second continuously-awake free service does not fit.

This is why `rockygpt-data` was retired rather than left idle: a second free service that anything wakes on a schedule does not fit alongside a continuously-awake brain. Keep the workspace to one continuously-awake free service.

Because the monitor treats any non-2xx as an outage, and free-plan monitors send `HEAD` with no way to change the method, any endpoint a monitor points at must answer `HEAD` as well as `GET`. `/health` does; `/readiness` and `/readiness/chat-logs` do not, and would report a false outage if pointed at today.

## Promotion and Deployment

1. Develop and verify changes on each repository's `dev` branch.
2. Run local and CI test suites (`pytest`, `typecheck`, `lint`, and Playwright suites).
3. Merge `dev` into `main` in dependency order: **brain, then UI**. `rockygpt-data` no longer deploys; publishing a dataset is a pipeline run, not a release.
4. After production deployment, verify service health using the `Service Smoke` workflow or local runner. The scheduled production monitor repeats the check every six hours.

### Retiring `rockygpt-data` (completed 2026-08-28)

Kept as a worked example, because the near-miss is the instructive part: a merge into `main` is not a deployment until it is **pushed**, and for most of this migration the working tree was two commits ahead of production in both repos. Confirm what production actually runs before acting on what the working tree says:

```
curl -s -o /dev/null -w '%{http_code}\n' https://rockygpt-brain.onrender.com/v1/map
curl -s -o /dev/null -w '%{http_code}\n' https://rockygpt.vercel.app/api/map
```

A 404 from the brain with a 200 from the UI means the UI is still being served by `rockygpt-data`, and removing that service would take every campus-data panel down with it. The order followed was: push brain `main`, confirm `/v1/map` answers 200, push UI `main`, confirm `/api/map` still answers 200, then delete the service.

`DATA_URL` was dropped from `tests/service-smoke.mjs` in the same change. Leaving it in is what would have turned a deliberate retirement into a recurring production-monitor incident every six hours. `rockygpt-data/render.yaml` has since been deleted too, so no blueprint can recreate the service. The `DATA_URL` secret is still worth removing from GitHub Actions and Vercel, where it is now unused.

## API compatibility

The brain owns the only deployed HTTP contract, in `rockygpt-brain/spec/brain-api.openapi.yaml`. Additive changes increment its minor version. A breaking change requires a preserved `/v1` implementation, a new `/v2` path, and a major OpenAPI version.

There is no SDK release. The generator tooling and the `sdk-release` and `openapi-breaking` workflows were removed from `rockygpt-data` on 2026-08-28 along with the service they described; no client is generated or published from any spec today. Reinstating that would mean writing the workflow again deliberately.

## Database changes

Use additive migrations first: add nullable/defaulted columns or new tables, deploy compatible readers, backfill, then remove obsolete fields in a later major release. Data and brain credentials must remain unable to access one another's schemas. Run the infra role-isolation check after changing grants.

## Secrets, incidents, rollback

Rotate secrets in downstream-first order: database credentials, then brain keys, then UI keys. During a two-value rotation, deploy accepting services before callers, verify, then revoke the old value. Never expose `ABUSE_HASH_KEY`, database URLs, or model keys to browser or native code.

For rollback, restore the last verified data release, roll brain back to the last compatible commit, then roll UI back. Do not roll an API owner behind a caller that requires a newer contract. On monitor failure, use its incident issue as the timeline, link deployments and smoke runs, and close it only after the recovery smoke passes.
