# rockygpt-infra

Deployment for RockyGPT.

## The repositories

RockyGPT is five separate repositories, not one project in folders:

    rockygpt-ui      the web app
    rockygpt-brain   the answering engine, behind POST /v1/chat
    rockygpt-data    campus ingestion and publication; deploys nothing
    rockygpt-evals   answer-quality suites
    rockygpt-infra   this one

They are separate on purpose. Each application builds from its own checkout.
Runtime collaboration happens through versioned HTTP endpoints, never sibling
source imports. Data owns publication in PostgreSQL; the restarted Brain reads
the active campus dataset without writing. UI and evals do not connect to
PostgreSQL. The restarted Brain exposes chat and probes; campus-panel and
chat-log endpoints are outside this checkpoint's contract.

## Local stack

The one local stack is the workspace's `run-local.sh` (rockygpt-workspace): the Brain serves
the same release graph file production serves, and the student and dev UIs talk to it over
HTTP. The old Docker Compose stack here was removed on 2026-11-03; it had not started since the
Node Brain was retired on 2026-08-28.

## Cross-service smoke

After deploying both services, verify the actual HTTP topology rather than only
the individual processes:

    UI_URL=https://… BRAIN_URL=https://… \
      node tests/service-smoke.mjs

Those two variables are the whole requirement, and deliberately so: the smoke
test no longer probes a campus-data service. Adding one back is what would turn
a deliberate retirement into a monitor incident every six hours.

The `Service Smoke` workflow runs the same check with repository secrets. It
checks UI and Brain readiness, that the Brain refuses chat without the shared
service token (401 `unauthorized`), and, when `SERVICE_TOKEN` is set,
that a malformed chat body is rejected (422). None of it makes a model call.
The restarted Brain exposes chat and probes; this smoke does not require the
retired map or chat-log endpoints. Run the conversations in
`rockygpt-evals/brain-reset` separately to verify answer quality.

Deployment, rollback, and incident procedures are in
[`docs/release-runbook.md`](docs/release-runbook.md), and the future native
security boundary is in [`docs/adr-native-client-security.md`](docs/adr-native-client-security.md).

## Layout

    deployment/   the split tooling, kept as a record
    docs/         operational notes

## About deployment/

`split-repos.sh` built these five repositories out of the original monorepo.
That job is finished, and the script refuses to run: against the live
repositories it would rebuild each one from scratch and force-push over
whatever has been committed since. It is kept for the record, not for use.
