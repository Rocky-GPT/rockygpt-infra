# RockyGPT application isolation

This document is the migration contract for separating the existing TypeScript
applications. It deliberately changes no language, framework, product feature,
or public response shape.

## Target ownership

| Repository | Owns | May call |
| --- | --- | --- |
| `rockygpt-ui` | Browser experience, browser session cookie, web-only presentation | Brain HTTP API, data HTTP API |
| `rockygpt-brain` | Chat orchestration, OpenAI, safety, grounding, conversation state, chat logs, feedback | Data HTTP API, its own database schema |
| `rockygpt-data` | Campus ingestion, releases, repository queries, public campus-data API | Its own database schema and source sites |
| `rockygpt-evals` | Black-box answer and contract evaluation | Brain and data HTTP APIs |
| `rockygpt-infra` | Local composition, deployment wiring, cross-service smoke tests | Published service interfaces |

No application may import another application's source or connect to another
application's database schema. Wire contracts are JSON over versioned HTTP
endpoints.

## Removed dependency inventory

### Brain to data

- `src/tools.ts` imports the repository factory and campus schemas.
- `src/safety.ts` imports the repository and source registry.
- `src/brain.ts` imports source types and map resolution.
- `src/shuttle.ts` imports shuttle schema types.
- `src/detect-origin.ts` imports the logger's `QuestionOrigin` type.
- `api/server.ts` imports the data package's chat logger.

Result: campus lookups and map resolution go through the data HTTP API; chat
persistence lives in the brain; wire types live inside the brain client.

### UI to brain

- The chat route imports the brain HTTP client, contract, and origin detector.
- The page imports the brain conversation-turn type.

Result: the UI owns its brain HTTP client and wire DTOs. Origin detection is a
browser-edge concern and lives in the UI.

### UI to data

- Menu, dining-hours, directory, artifacts, readiness, logs, feedback, entity
  registry, scrape status, and data explorer routes import data implementation.
- Map, shuttle, directory, calendar, and entity components import data constants
  or types.
- Several server routes connect directly to PostgreSQL.

Result: public campus reads go to the data HTTP API. Development data
views proxy the development-only data endpoints. Chat logs and feedback go to
the brain HTTP API. Presentation DTOs live in the UI.

### Evals to brain and data

The suites import the answering engine, safety and grounding helpers,
conversation stores, repositories, schemas, and database pools.

Result: product-quality suites drive the HTTP APIs. Deterministic privacy tests
live in the brain repository that owns the helper.

## Enforced boundaries

- UI has no database driver, database URL, or brain/data package dependency.
- Brain has no data package dependency; `DATA_URL` is its only campus-data path.
- Data exposes only campus-data and development inspection endpoints. Historic
  feedback migrations remain for safe upgrades, but no runtime reads or writes
  those legacy tables.
- Evals have no database driver, model SDK, or application package dependency.
- Each application CI checks out and installs only its own repository.

## Compatibility baseline

The migration must preserve:

- `POST /v1/chat` request, success, and failure bodies.
- Citation source identifiers, titles, URLs, and collection timestamps.
- Current tool selection, three-round bound, grounding refusal, and safety
  behavior.
- Campus time calculations in `America/New_York`, including a caller-pinned
  instant.
- Existing web routes until each caller has moved to its service endpoint.
- Current public menu, directory, map, shuttle, artifact, feedback, health, and
  readiness response shapes.

## Cutover rules

1. Add and contract-test an HTTP replacement.
2. Move one caller and verify response parity.
3. Keep the old path for one verification step.
4. Remove duplicate code and the package import.
5. Make the affected repository pass from a standalone checkout.

Administrative chat data is never part of the public campus-data surface.
Before any broader API cutover, log reads and mutations must be authenticated,
stored text must be redacted, persistent visitor identifiers must be hashed,
and transcript expiry must be enforced.
