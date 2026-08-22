# rockygpt-infra

Deployment for RockyGPT.

## The repositories

RockyGPT is five separate repositories, not one project in folders:

    rockygpt-ui      the web app
    rockygpt-brain   the answering engine, behind POST /v1/chat
    rockygpt-data    campus data, behind its own HTTP service
    rockygpt-evals   answer-quality suites
    rockygpt-infra   this one

They are separate on purpose. Each application builds from its own checkout.
Runtime collaboration happens through versioned HTTP endpoints, never sibling
source imports. Data and brain each own their PostgreSQL schema; UI and evals do
not connect to PostgreSQL.

## Local stack

    cd docker
    cp .env.example .env      # set the API key and random secrets
    docker compose up --build

The key is read to interpolate the stack rather than only to start it, so it
has to be present for every compose command — `ps` and `logs` included. A
`.env` beside the compose file is picked up automatically and saves passing it
each time.

Postgres, the data service on :8100, the brain on :8000, and the web app on
:3000, in the same topology as production: a browser reaches the web app, and
the web app reaches the brain over the network rather than importing it. Ports
bind to loopback so the development stack is not exposed to the local network.

Each image builds from its own repository, which must be checked out beside
this one. There is no shared root, workspace install, or cross-repository npm
dependency.

A one-shot `schema` service creates the campus-data tables and exits. Brain
creates its isolated persistence tables when it first needs them. PostgreSQL is
bootstrapped with separate `rockygpt_data` and `rockygpt_brain` roles; neither
role has privileges on the other application's schema.

Use the same random, 32-character-or-longer `ABUSE_HASH_KEY` in UI and brain.
UI signs its pseudonymous rate-limit identity with that key; brain verifies the
signature before trusting it. The browser never receives the key or a raw IP.

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

After deploying all three services, verify the actual HTTP topology rather than
only the individual processes:

    UI_URL=https://… BRAIN_URL=https://… DATA_URL=https://… \
      node tests/service-smoke.mjs

The `Service Smoke` workflow runs the same check with repository secrets. It
checks readiness, the direct and UI-proxied map contract, and malformed chat
handling without making a model call.

## Layout

    docker/       Dockerfiles and the compose stack
    deployment/   the split tooling, kept as a record
    docs/         operational notes

## About deployment/

`split-repos.sh` built these five repositories out of the original monorepo.
That job is finished, and the script refuses to run: against the live
repositories it would rebuild each one from scratch and force-push over
whatever has been committed since. It is kept for the record, not for use.
