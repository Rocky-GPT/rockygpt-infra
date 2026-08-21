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
    OPENAI_API_KEY=... docker compose up --build

Postgres, the brain on :8000, and the web app on :3000, in the same topology
as production: a browser only reaches the web app, and the web app reaches the
brain over the network rather than importing it.

## Layout

    docker/       Dockerfiles and the compose stack
    deployment/   the split tooling, kept as a record
    docs/         operational notes

## About deployment/

`split-repos.sh` built these five repositories out of the original monorepo.
That job is finished, and the script refuses to run: against the live
repositories it would rebuild each one from scratch and force-push over
whatever has been committed since. It is kept for the record, not for use.
