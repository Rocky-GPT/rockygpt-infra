# RockyGPT release runbook

## Environment topology

Development happens on `dev` across repositories. Merges into `main` automatically deploy production services:
- **UI**: Vercel production deployment (`rockygpt.danielrajakumar.com`). Each release adds a line at the top of `rockygpt-ui/lib/versions.ts` (About page "Versions").
- **Brain**: Render Python service (`rockygpt-brain.onrender.com`), Starter plan ($7/month, always on since 2026-11-01). Render deploys a `main` commit only after its "Release checks" check run passes.
- **Data**: no service. `rockygpt-data` publishes datasets from its scheduled workflows on `main`; publishing is a pipeline run, not a release.
- **Dev UI** (`rockygpt-dev`) and the laptop launcher (`rockygpt-workspace`) run only on Dan's laptop.

**Campus data path (same on the laptop and in production):** the Brain answers only from the release graph file and its meaning index, never from Neon. The daily data run builds them once and puts them in Cloudflare R2 (`rockygpt-raw-artifacts`, prefix `brain-graph/`, pointer `active.json` last). Production's Brain downloads that copy into `/tmp/rockygpt-graph` before it takes questions and checks for a newer one every 5 minutes; the laptop downloads the same copy only on `./run-local.sh sync-prod` or the dev UI's "Sync from production". If R2 is unreachable at a deploy, `/readiness` stays 503 and Render keeps the old instance serving. Rollback: move `BRAIN_GRAPH_ONLY_DIR` from `settings` to `retired` for brain-production; after the sync the Brain reads Neon (`DATABASE_URL`) as before.

The Brain no longer sleeps, so cold starts happen only right after a deploy. At startup it loads its meaning index and per-release lists before taking questions (at most 120 s; `BRAIN_WARM_AT_START=0` turns this off).

An UptimeRobot monitor, `RockyGPT brain (down alert)`, sends `HEAD /health` every 5 minutes and emails Dan when the Brain is down. It lives in Dan's UptimeRobot account, not in any repository. Free-plan monitors send `HEAD`, so any endpoint a monitor points at must answer `HEAD` as well as `GET`: `/health` does, and `/readiness` does not.

## Settings and secrets

Production settings come from one place: `config/environment-sync.json` here, plus the RockyGPT 1Password Environment for secret values. The `Environment Sync` workflow on `main` writes Render, Vercel and GitHub Actions secrets, then redeploys Render and Vercel if anything changed. It runs on a 30-minute schedule (GitHub often runs it hours late) and by hand with **Run workflow** (`apply` ticked to write; unticked is a dry run).

- To **add** a setting, add it to the manifest and, if it is secret, to 1Password under the stored name the manifest maps.
- To **remove** one, delete it from the manifest and list its name under that target's `retired`; the next sync deletes it from the provider.
- To **rename** one, first teach the readers both names, release that, then switch the manifest destination and retire the old name, then drop the fallback. That is how `STAGING_SERVICE_TOKEN` became `SERVICE_TOKEN` on 2026-11-02.
- Rename a 1Password item only after the manifest that reads the new name is on `main`; until then the sync stops on the missing name (it changes nothing, but it pauses).

## Dates that expire

- The bundled gpt-6-luna price (`rockygpt-brain/src/rockygpt_brain/provider-release.json`) expires monthly, and the Brain stops answering when it does (production was down 17 hours on 2026-11-01). `rockygpt-brain/.github/workflows/price-renewal.yml` checks OpenAI's page daily; when the price is unchanged and 10 days or fewer remain it opens renewal PRs into `main` and `dev`. **Merge the `main` one.** A changed price, an unreadable page, or 5 days or fewer left opens a `price-check` issue.
- Everything else (the embedding price setting, tokens) is in `config/expiries.json` or is a `*_VALID_UNTIL` setting, and the `Expiry Watch` workflow opens an `expiry-watch` issue 21 days ahead.

## Promotion and Deployment

1. Develop and verify changes on each repository's `dev` branch.
2. Run local and CI test suites (`pytest`, `typecheck`, `lint`, and Playwright suites).
3. Merge `dev` into `main` in dependency order: **brain, then UI**. Keep `dev` and `main` identical in every repository after a release.
4. After production deployment, verify service health using the `Service Smoke` workflow or local runner. The scheduled production monitor repeats the check every six hours. Both need the `SERVICE_TOKEN` secret for the full check.

### Retiring `rockygpt-data` (completed 2026-08-28)

Kept as a worked example, because the near-miss is the instructive part: a merge into `main` is not a deployment until it is **pushed**, and for most of this migration the working tree was two commits ahead of production in both repos. Confirm what production actually runs before acting on what the working tree says:

```
curl -s -o /dev/null -w '%{http_code}\n' https://rockygpt-brain.onrender.com/v1/map
curl -s -o /dev/null -w '%{http_code}\n' https://rockygpt.danielrajakumar.com/api/map
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
