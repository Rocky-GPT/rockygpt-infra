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

    cd docker
    cp .env.example .env      # set the API key and random secrets
    docker compose up --build

The key is read to interpolate the stack rather than only to start it, so it
has to be present for every compose command — `ps` and `logs` included. A
`.env` beside the compose file is picked up automatically and saves passing it
each time.

Postgres, the brain on :8000, and the web app on :3000, in the same topology as
production: a browser reaches the web app, and the web app reaches the brain over
the network rather than importing it. Ports bind to loopback so the development
stack is not exposed to the local network.

**This stack does not currently come up.** `Dockerfile.brain` built the retired
Node brain and was removed on 2026-08-28; nothing has replaced it, so the compose
`brain` service has no image to build. `docker-compose.yml` also still defines the
retired `data` service and passes a `DATA_URL` the brain ignores. Until that is
settled, `../run-local.sh start` from the workspace root is the working local
stack — it runs the brain and the web app against the configured database.

Each image builds from its own repository, which must be checked out beside
this one. There is no shared root, workspace install, or cross-repository npm
dependency.

A one-shot `schema` service creates the campus-data tables and exits. The
restarted Brain does not create persistence tables. The existing role bootstrap
predates this restart: a future stack update must grant the Brain role read-only
access to the published `rockygpt_v2` schema. Data retains publication ownership.

The UI uses `ABUSE_HASH_KEY` for pseudonymous, per-process rate limiting; the
restarted Brain does not verify client-identity signatures. Use a random key of
at least 32 characters in production and never expose it to the browser. Optional
environment access uses `STAGING_SERVICE_TOKEN`, shared by Brain and its clients.

The role bootstrap runs only for a new PostgreSQL volume. An existing local
volume predates these roles. Back it up if it contains anything you need, then
recreate that development volume before expecting the isolated credentials to
work.

That leaves the schema without any campus data in it. The stack starts, and
every lookup finds nothing, because a release is published rather than seeded.
To fill it, run the pipeline against the same database from `rockygpt-data`:

    DATABASE_URL=postgres://rockygpt_data:rockygpt_data@localhost:5432/rockygpt \
      npm run data:bootstrap

which needs the `RAW_ARTIFACT_*` credentials that reach the archive.

## Cross-service smoke

After deploying both services, verify the actual HTTP topology rather than only
the individual processes:

    UI_URL=https://… BRAIN_URL=https://… \
      node tests/service-smoke.mjs

Those two variables are the whole requirement, and deliberately so: the smoke
test no longer probes a campus-data service. Adding one back is what would turn
a deliberate retirement into a monitor incident every six hours.

The `Service Smoke` workflow runs the same check with repository secrets. It
checks UI and Brain readiness and malformed chat
handling without making a model call.
The restarted Brain exposes chat and probes; this smoke does not require the
retired map or chat-log endpoints. Run the conversations in
`rockygpt-evals/brain-reset` separately to verify answer quality.

Deployment, rollback, and incident procedures are in
[`docs/release-runbook.md`](docs/release-runbook.md), and the future native
security boundary is in [`docs/adr-native-client-security.md`](docs/adr-native-client-security.md).

## Layout

    docker/       Dockerfiles and the compose stack
    deployment/   the split tooling, kept as a record
    docs/         operational notes

## About deployment/

`split-repos.sh` built these five repositories out of the original monorepo.
That job is finished, and the script refuses to run: against the live
repositories it would rebuild each one from scratch and force-push over
whatever has been committed since. It is kept for the record, not for use.
