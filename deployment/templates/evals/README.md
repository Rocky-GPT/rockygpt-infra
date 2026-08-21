# rockygpt-evals

Answer-quality suites for RockyGPT.

Each suite drives real turns and asserts on what comes back — that answers are
grounded in retrieved evidence, that refusals happen when they should, that
follow-ups keep their referent, and that deterministic questions stay
deterministic.

## Running

    npm install
    cp .env.example .env
    npm run test:grounding
    npm run test:torture

Suites import the engine directly, so they need the same environment the brain
does. To run them against a deployed service instead, set `BRAIN_URL` and use
the client in `@rockygpt/brain/api/client`.
