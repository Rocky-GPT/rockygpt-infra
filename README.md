# rockygpt-infra

Deployment for RockyGPT.

## The repositories

RockyGPT is five separate repositories, not one project in folders:

    rockygpt-ui      the web app
    rockygpt-brain   the answering engine, behind POST /v1/chat
    rockygpt-data    campus data, behind its own HTTP service
    rockygpt-evals   answer-quality suites
    rockygpt-infra   this one

They are separate on purpose. Each is meant to be replaceable on its own —
the brain is expected to be rewritten in another language — so the contracts
between them are HTTP and the PostgreSQL schema, never shared source.

Where they do depend on each other today, it is a git dependency pinned to a
commit. Changing `rockygpt-data` therefore does not reach its consumers until
they run:

    npm update @rockygpt/data

## Local stack

    cd docker
    cp .env.example .env      # set OPENAI_API_KEY
    docker compose up --build

The key is read to interpolate the stack rather than only to start it, so it
has to be present for every compose command — `ps` and `logs` included. A
`.env` beside the compose file is picked up automatically and saves passing it
each time.

Postgres, the data service on :8100, the brain on :8000, and the web app on
:3000, in the same topology as production: a browser reaches the web app, and
the web app reaches the brain over the network rather than importing it.

Each image builds from its own repository, which must be checked out beside
this one. There is no shared root to build from and no workspace to install
from — the packages reach each other as ordinary dependencies, fetched from
GitHub during the build.

A one-shot `schema` service creates the tables and exits; the others wait for
it, so a first run against an empty volume does not race an empty database.

That leaves the schema without any campus data in it. The stack starts, and
every lookup finds nothing, because a release is published rather than seeded.
To fill it, run the pipeline against the same database from `rockygpt-data`:

    DATABASE_URL=postgres://rockygpt:rockygpt@localhost:5432/rockygpt \
      npm run data:bootstrap

which needs the `RAW_ARTIFACT_*` credentials that reach the archive.

## Layout

    docker/       Dockerfiles and the compose stack
    deployment/   the split tooling, kept as a record
    docs/         operational notes

## About deployment/

`split-repos.sh` built these five repositories out of the original monorepo.
That job is finished, and the script refuses to run: against the live
repositories it would rebuild each one from scratch and force-push over
whatever has been committed since. It is kept for the record, not for use.
