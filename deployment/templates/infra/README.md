# rockygpt-infra

Deployment for RockyGPT.

## Local stack

    cd docker
    OPENAI_API_KEY=... docker compose up --build

Brings up Postgres, the brain on :8000, and the web app on :3000, in the same
topology as production: the browser only reaches the web app, and the web app
reaches the brain over the network rather than importing it.

## Layout

    docker/       Dockerfiles and the compose stack
    deployment/   environment configuration
    docs/         operational notes
