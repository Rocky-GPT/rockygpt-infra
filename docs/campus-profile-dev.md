# Campus profile development activation

The repository `.env` campus URL can point to the shared campus database. Do not
run the normal Data publisher there for a development-only change: publication
swaps its active release for every consumer. Use a new isolated local database
and retain the old connection for rollback.

`scripts/clone-campus-for-dev.py` reads the active public campus dataset in a
read-only, repeatable-read transaction. It copies public records and their
original IDs, sources, collection times and validity dates into a **new** local
database, then installs the matching compiler artifacts in one transaction:

- `campus-identities.json`
- `campus-identity-coverage.json`
- `catalog-conveners.json`

Club/event releases may also include `event-organizers.json`. This optional
artifact keeps explicit organizer assertions and their actual page-capture
timestamps separate from the event listing. The loader requires matching
original event row IDs whenever the identity map includes organizer links;
matching a repeated title/date key alone is insufficient. Existing three-file
bundles remain supported.

Building releases may add `campus-buildings.json`. Building identities must link
to map locations in that artifact, and every `office_at` or `located_at`
relationship must target a building and cite a contact's `office` field. A
source release published before the campus map became a source has no
`campus-map` source row; the loader then creates it from the artifact's `source`
block, with a static source run that keeps the map's own collection time
(`map_generated_at`) rather than the load time.

Requirement releases may add `catalog-course-identities.json` and
`program-requirement-groups.json`. The loader recomputes every course ID with
the preserved derivation (UUIDv5 over Python's
`json.dumps(["rockygpt", "course", source_key, code])`) and refuses any
difference. Requirement edges must point to programs in the identity map,
groups in the bundle and courses in the course artifact, and requirement
groups need the course artifact beside them.

The coverage counts must agree with the identity map. Convener and
`listed_faculty` relationships must cite a program whose own Convener
(`rJQmj`) or Program Faculty (`xiQxl`) field is in `catalog-conveners.json`.
Deploy a Brain that knows every relationship type and identity kind in the
bundle before activating it: the Brain refuses an identity map with a type it
does not know, which makes every profile unavailable. Pass the source version
used for compilation so a publication between export and load fails explicitly.
The loader does not regenerate collection timestamps or claim fresh source
verification. It is a public development snapshot with a profile overlay, not a
full archival release restore: private records, raw ingestion archives, and the
original release graph are not copied.

From the infra repository, after compiling against the intended public snapshot:

```sh
../rockygpt-brain/.venv/bin/python scripts/clone-campus-for-dev.py \
  --source-env ../rockygpt-data/.env \
  --target postgresql://postgres@127.0.0.1:55434/rockygpt_profiles_dev_candidate \
  --data-root ../rockygpt-data \
  --artifact-dir /tmp/rockygpt-identities \
  --expected-source-version SOURCE_DATASET_VERSION \
  --version dev-profiles-CANDIDATE \
  --data-commit DATA_COMMIT_SHA \
  --report /tmp/rockygpt-profile-candidate.json
```

Replace the candidate/version/commit placeholders with the reviewed values.
The target must be an explicitly named `127.0.0.1` database with the
`rockygpt_profiles_dev_` prefix and a port. Existing databases and report files
are rejected. Connection service, host-address and options overrides are
rejected. The compatibility option `--identity-artifact FILE` loads a single
identity map for the original pilot; use `--artifact-dir` for full profiles.

The loader grants `brain_campus_reader` SELECT on the public campus tables only.
Activate committed Brain code with the bounded development launcher:

```sh
../rockygpt-brain/.venv/bin/python scripts/deploy-profile-brain-dev.py \
  --revision FULL_40_CHARACTER_BRAIN_COMMIT_SHA \
  --database postgresql://brain_campus_reader@127.0.0.1:55434/rockygpt_profiles_dev_candidate \
  --expected-dataset dev-profiles-CANDIDATE
```

The launcher archives the explicit commit into
`../.local-logs/profile-feature/brain-builds/<sha>`, checks its integrity, and
makes the files read-only. It uses the existing virtualenv with an absolute
`PYTHONPATH` pointing at the archive; it disables bytecode writes and runs without
reload. Other tasks can continue editing the Brain checkout without changing the
running code or breaking its configuration hash. No branches are switched and
uncommitted files are never included in the archive.

Before restarting, it verifies the local reader and expected dataset, imports
the configuration from the archive, and checks the saved PID's command and
working directory. It sends SIGTERM only to that local Brain PID, allows graceful
shutdown, and refuses to kill another process on port 8000. It changes only
`DATABASE_URL` and `BRAIN_EXPECTED_CONFIG_HASH` in the ignored Brain `.env`;
the development provider and accounting settings remain unchanged. The original
environment backup at `../.local-logs/profile-feature/brain.env.before` is retained.

Activation succeeds only when `/readiness` reports the expected archive hash and
dataset. The safe activation receipt is saved in
`../.local-logs/profile-feature/activations/`; `active-brain.json` records the
latest launch. Verify normal chat responses after this startup check. The
general workspace `run-local.sh` still runs a mutable development checkout;
rerun the immutable launcher to restore this verified feature deployment.

The dedicated PostgreSQL cluster created for this session is at
`../.local-logs/profile-postgres-20260921`, bound to loopback port 55434. When it
is stopped, start it using `/opt/homebrew/opt/postgresql@17/bin/pg_ctl`, that data
directory, an explicit log path, and `-o '-p 55434 -h 127.0.0.1 -k /tmp'`.
Do not reset Docker volumes or reuse another database to recover it.

For rollback, rerun the immutable launcher with the preceding verified Brain
commit, local candidate database, and expected dataset from its activation
receipt. The prior database and shared source remain unchanged. For a data-only
rollback, keep the compatible Brain commit and select the preceding isolated
candidate database. Existing archive directories without a valid manifest, or
with modified files, are rejected; preserve any earlier diagnostic archive under
a separate backup name before preparing that commit. No production pointer
swap, source checkout, or destructive SQL is needed.

## Client acceptance

Student UI `http://127.0.0.1:3000` sends messages through `/api/chat`; Dev UI
`http://127.0.0.1:3100/brain/ask` sends them through `/api/brain/chat`. Both proxy
the successful Brain response without filtering profile tools. After activation,
run a profile question through each browser and record the request ID, dataset
version, tool trace and displayed answer. Student chat should show the answer and
citations; tool arguments, coverage and diagnostics belong in Dev's TOOL CALLS
and complete-response panels. Do not expose diagnostics in the Student layout.

Run loader and immutable-launcher guards without database access or service
restarts:

```sh
../rockygpt-brain/.venv/bin/python -m unittest discover -s tests \
  -p 'test_*.py' -v
```

This document describes the activation procedure. Successful activation and
browser acceptance require the actual recorded checks for the chosen candidate.
