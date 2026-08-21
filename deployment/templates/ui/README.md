# rockygpt-ui

The RockyGPT web app: routes, API handlers, and React components.

Answering happens in `rockygpt-brain`, reached over HTTP. What lives here is the
browser's edge of the product — the chat surface, the modals, and the handlers
that serve campus data straight from `rockygpt-data`.

## Running

    npm install
    cp .env.example .env      # set BRAIN_URL and DATABASE_URL
    npm run dev

The brain must be running for chat to work. With it down, `/api/chat` answers
503 rather than falling back to an ungrounded reply.

## Static campus data

`public/data/` is published by the data pipeline, not authored here, and is
gitignored for that reason. Generate it from `rockygpt-data`:

    ROCKY_PUBLIC_DIR=../rockygpt-ui/public npm run data:publish
