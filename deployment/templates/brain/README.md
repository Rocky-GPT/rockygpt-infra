# rockygpt-brain

The RockyGPT answering engine, and the HTTP service that fronts it.

## Layout

    src/    the engine: retrieval, tools, grounding, safety, conversation state
    api/    the service — contract, server, and a typed client for callers

## The contract

`api/contract.ts` is the whole agreement between this service and its callers.
It imports nothing, so a caller can depend on the shapes without pulling in the
engine, the OpenAI client, or a database driver.

The package exports `./api/*` and `./src/*` separately. Callers that should only
speak HTTP import the first; the eval suites, which test internals, import the
second.

## Running

    npm install
    cp .env.example .env      # set OPENAI_API_KEY and DATABASE_URL
    npm run dev               # watch mode on :8000

    curl -s localhost:8000/health

## Endpoints

    GET  /health     liveness, for probes and compose healthchecks
    POST /v1/chat    one turn — see api/contract.ts for both bodies

A turn that cannot be grounded answers 503 rather than guessing.
