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

The coverage counts must agree with the identity map; convener relationships
must reference catalog evidence present in the bundle. Pass the source version
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

After loading, point the development Brain's campus `DATABASE_URL` to
`postgresql://brain_campus_reader@127.0.0.1:55434/rockygpt_profiles_dev_candidate`.
The loader grants that role SELECT on the public campus tables only. Keep the
separate development accounting/provider configuration unchanged. Restart the
local Brain through the workspace workflow, then verify `/readiness` and actual
chat responses expose the expected configuration hash and dataset version.

The dedicated PostgreSQL cluster created for this session is at
`../.local-logs/profile-postgres-20260921`, bound to loopback port 55434. When it
is stopped, start it using `/opt/homebrew/opt/postgresql@17/bin/pg_ctl`, that data
directory, an explicit log path, and `-o '-p 55434 -h 127.0.0.1 -k /tmp'`.
Do not reset Docker volumes or reuse another database to recover it.

For rollback, restore the saved development campus connection and restart the
compatible Brain code. The prior database and shared source remain unchanged.
If rolling back only the profile candidate, select the preceding isolated
candidate database instead. No production pointer swap or destructive SQL is
needed.

## Client acceptance

Student UI `http://127.0.0.1:3000` sends messages through `/api/chat`; Dev UI
`http://127.0.0.1:3100/brain/ask` sends them through `/api/brain/chat`. Both proxy
the successful Brain response without filtering profile tools. After activation,
run a profile question through each browser and record the request ID, dataset
version, tool trace and displayed answer. Student chat should show the answer and
citations; tool arguments, coverage and diagnostics belong in Dev's TOOL CALLS
and complete-response panels. Do not expose diagnostics in the Student layout.

Run loader guards without database access:

```sh
../rockygpt-brain/.venv/bin/python -m unittest discover -s tests \
  -p 'test_clone_campus_for_dev.py' -v
```

This document describes the activation procedure. Successful activation and
browser acceptance require the actual recorded checks for the chosen candidate.
