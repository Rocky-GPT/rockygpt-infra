"""Load an unchanged public campus snapshot into a NEW localhost dev database.

Run with the Brain virtualenv (psycopg and python-dotenv). Never updates the
source. A separate database per candidate preserves original record IDs and
makes rollback a connection change instead of a destructive migration.
"""

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import certifi
from dotenv import dotenv_values
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


TABLES = (
    "sources", "source_runs", "critical_facts", "campus_contacts", "campus_hours",
    "dining_hours", "menu_items", "shuttle_routes", "shuttle_trips", "academic_dates",
    "campus_events", "clubs", "programs", "documents", "document_chunks", "release_artifacts",
)
PROFILE_ARTIFACTS = ("campus-identities", "campus-identity-coverage", "catalog-conveners")
OPTIONAL_PROFILE_ARTIFACTS = ("event-organizers",)


def local_target(value: str) -> dict[str, str]:
    parts = conninfo_to_dict(value)
    if (
        set(parts) - {"host", "port", "dbname", "user", "password", "sslmode"}
        or any(os.getenv(key) for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS"))
        or parts.get("host") != "127.0.0.1"
        or not re.fullmatch(r"rockygpt_profiles_dev_[a-z0-9_]+", parts.get("dbname", ""))
        or len(parts.get("dbname", "")) > 63
        or not parts.get("port", "").isdigit()
        or not 1 <= int(parts.get("port", "0")) <= 65535
        or parts.get("sslmode", "disable") != "disable"
        or parts.get("options")
    ):
        raise ValueError("Target must be an explicitly named localhost rockygpt_profiles_dev_* database")
    return {**parts, "hostaddr": "127.0.0.1", "sslmode": "disable"}


def load_artifacts(identity_artifact: Path | None, artifact_dir: Path | None) -> tuple[dict, dict]:
    """Load one pilot map or the complete compiler output, without adding dates."""
    if (identity_artifact is None) == (artifact_dir is None):
        raise ValueError("Choose exactly one of --identity-artifact and --artifact-dir")
    paths = ({key: artifact_dir / f"{key}.json" for key in PROFILE_ARTIFACTS}
             if artifact_dir is not None else {"campus-identities": identity_artifact})
    if artifact_dir is not None:
        paths.update({key: artifact_dir / f"{key}.json" for key in OPTIONAL_PROFILE_ARTIFACTS
                      if (artifact_dir / f"{key}.json").exists()})
    artifacts, hashes = {}, {}
    for key, path in paths.items():
        raw = path.read_bytes()
        artifacts[key] = json.loads(raw)
        hashes[key] = hashlib.sha256(raw).hexdigest()
        if not isinstance(artifacts[key], dict):
            raise ValueError(f"Expected an object for {key}")
    identity = artifacts["campus-identities"]
    if identity.get("schema_version") != 1 or not isinstance(identity.get("entities"), list):
        raise ValueError("Expected a compiled campus identity artifact")
    if artifact_dir is not None:
        coverage = artifacts["campus-identity-coverage"]
        kinds, links, relationships = Counter(), Counter(), Counter()
        for entity in identity["entities"]:
            kinds[entity["kind"]] += 1
            for link in entity["links"]:
                links[link["collection"]] += len(link["source_record_keys"])
            for relationship in entity.get("relationships", []):
                relationships[relationship["type"]] += 1
        expected = {"identity_count": len(identity["entities"]), "identities_by_kind": dict(kinds),
                    "linked_records": dict(links), "relationships": dict(relationships)}
        if any(coverage.get(key) != value for key, value in expected.items()) or not isinstance(coverage.get("unresolved"), list):
            raise ValueError("Coverage artifact does not match the compiled identity map")
        conveners = artifacts["catalog-conveners"]
        if not isinstance(conveners.get("programs"), list) or "collected_at" not in conveners or not isinstance(conveners.get("source_url"), str):
            raise ValueError("Expected the compiler's catalog-conveners artifact")
        explicit_urls = {row.get("catalogUrl") for row in conveners["programs"]
                         if isinstance(row, dict) and isinstance(row.get("customFields", {}).get("rJQmj"), str)}
        for entity in identity["entities"]:
            for relationship in entity.get("relationships", []):
                if relationship["type"] == "convener" and any(
                    evidence.get("source_url") not in explicit_urls
                    for evidence in relationship.get("evidence", [])
                ):
                    raise ValueError("Convener relationships do not match the catalog evidence artifact")
        organizers = artifacts.get("event-organizers", {"schema_version": 1, "events": []})
        if organizers.get("schema_version") != 1 or not isinstance(organizers.get("events"), list):
            raise ValueError("Expected the event-organizers evidence artifact")
        for row in organizers["events"]:
            if not isinstance(row, dict) or any(not isinstance(row.get(key), str) or not row[key].strip()
                    for key in ("source_key", "source_record_key", "source_record_id", "event_url",
                                "organizer_url", "collected_at", "source_url")):
                raise ValueError("Event organizer evidence requires original record references and source capture time")
            try:
                captured = datetime.fromisoformat(row["collected_at"].replace("Z", "+00:00"))
                if captured.tzinfo is None:
                    raise ValueError("Missing capture timezone")
            except ValueError as error:
                raise ValueError("Event organizer evidence has an invalid source capture time") from error
        for entity in identity["entities"]:
            for relationship in entity.get("relationships", []):
                if relationship["type"] != "organized_by":
                    continue
                evidence = relationship.get("evidence", [])
                if not evidence or any(not any(
                    ref.get("collection") == "events"
                    and ref.get("source_key") == row["source_key"]
                    and ref.get("source_record_key") == row["source_record_key"]
                    and ref.get("source_record_id") == row["source_record_id"]
                    and ref.get("source_url") in {row["source_url"], row["event_url"]}
                    for row in organizers["events"]
                ) for ref in evidence):
                    raise ValueError("Event organizer relationships do not match the source evidence artifact")
    return artifacts, hashes


def expected_source(dataset: dict | None, expected_version: str | None) -> dict:
    if not dataset:
        raise ValueError("Source has no active dataset")
    if expected_version is not None and dataset["version"] != expected_version:
        raise ValueError("Source active dataset changed; re-export and compile before cloning")
    return dataset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-env", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    artifact_input = parser.add_mutually_exclusive_group(required=True)
    artifact_input.add_argument("--identity-artifact", type=Path, help="CSI pilot compatibility: identity map only")
    artifact_input.add_argument("--artifact-dir", type=Path, help="Directory containing the three matching compiler JSON artifacts")
    parser.add_argument("--expected-source-version", help="Refuse a source release different from the compiler's snapshot")
    parser.add_argument("--version", required=True)
    parser.add_argument("--data-commit", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    target_parts = local_target(args.target)
    if not args.version.startswith("dev-profiles-"):
        raise ValueError("Version must identify a dev-profiles candidate")
    if args.report.exists():
        raise ValueError("Report already exists; preserve the earlier candidate report")
    source_url = dotenv_values(args.source_env).get("DATABASE_URL")
    if not source_url:
        raise ValueError("The source environment must define DATABASE_URL")
    artifacts, artifact_hashes = load_artifacts(args.identity_artifact, args.artifact_dir)
    identity = artifacts["campus-identities"]
    identity_hash = artifact_hashes["campus-identities"]

    counts = {}
    source_options = conninfo_to_dict(source_url)
    source_options.setdefault("sslrootcert", certifi.where())
    with psycopg.connect(**source_options, row_factory=dict_row, connect_timeout=10) as source:
        source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        source.execute("SET LOCAL statement_timeout='30s'")
        dataset = expected_source(source.execute(
            "SELECT * FROM rockygpt_v2.dataset_versions WHERE status='active'"
        ).fetchone(), args.expected_source_version)
        source_version = dataset["version"]
        dataset_id = dataset["id"]
        # Validate the source snapshot before creating anything; never reuse a database.
        admin_parts = {**target_parts, "dbname": "postgres"}
        with psycopg.connect(make_conninfo(**admin_parts), autocommit=True) as admin:
            if admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (target_parts["dbname"],)).fetchone():
                raise ValueError("Target database already exists; choose a new candidate database")
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_parts["dbname"])))
        with psycopg.connect(make_conninfo(**target_parts), row_factory=dict_row) as target:
            target.execute("CREATE SCHEMA rockygpt_v2")
            target.execute((args.data_root / "src/data-v2/schema.sql").read_text())
            for migration in sorted((args.data_root / "src/data-v2/migrations").glob("[0-9]*.sql")):
                target.execute(migration.read_text())
                target.execute("INSERT INTO rockygpt_v2.schema_migrations(version) VALUES(%s)", (migration.name,))
            target.execute(
                "INSERT INTO rockygpt_v2.dataset_versions "
                "(id,version,status,source_commit_sha,quality_summary) VALUES(%s,%s,'staging',%s,%s)",
                (dataset_id, args.version, args.data_commit, Jsonb({
                    "dev_clone": True, "source_dataset_version": source_version,
                    "source_dataset_id": str(dataset_id), "source_commit_sha": dataset.get("source_commit_sha"),
                    "identity_hash": identity_hash,
                    "profile_artifact_hashes": artifact_hashes,
                    "source_quality_summary": dataset.get("quality_summary"),
                })),
            )
            for table in TABLES:
                if table == "sources":
                    rows = source.execute("SELECT * FROM rockygpt_v2.sources").fetchall()
                elif table == "document_chunks":
                    rows = source.execute(
                        "SELECT c.* FROM rockygpt_v2.document_chunks c JOIN rockygpt_v2.documents d "
                        "ON d.id=c.document_id WHERE d.dataset_version_id=%s", (dataset_id,),
                    ).fetchall()
                else:
                    rows = source.execute(
                        sql.SQL("SELECT * FROM rockygpt_v2.{} WHERE dataset_version_id=%s").format(sql.Identifier(table)),
                        (dataset_id,),
                    ).fetchall()
                columns = target.execute(
                    "SELECT column_name,data_type,is_generated FROM information_schema.columns "
                    "WHERE table_schema='rockygpt_v2' AND table_name=%s ORDER BY ordinal_position", (table,),
                ).fetchall()
                generated = {c["column_name"] for c in columns if c["is_generated"] == "ALWAYS"}
                json_columns = {c["column_name"] for c in columns if c["data_type"] in {"json", "jsonb"}}
                if rows:
                    keys = [key for key in rows[0] if key not in generated]
                    statement = sql.SQL("INSERT INTO rockygpt_v2.{} ({}) VALUES ({})").format(
                        sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, keys)),
                        sql.SQL(",").join(sql.Placeholder() for _ in keys),
                    )
                    with target.cursor() as cursor:
                        cursor.executemany(statement, [tuple(
                            Jsonb(row[key]) if key in json_columns and row[key] is not None else row[key] for key in keys
                        ) for row in rows])
                counts[table] = len(rows)
                print(f"Copied {table}: {len(rows)}", flush=True)
            for key, payload in artifacts.items():
                target.execute(
                    "INSERT INTO rockygpt_v2.release_artifacts(dataset_version_id,artifact_key,payload,content_hash) "
                    "VALUES(%s,%s,%s,%s) ON CONFLICT(dataset_version_id,artifact_key) "
                    "DO UPDATE SET payload=excluded.payload,content_hash=excluded.content_hash",
                    (dataset_id, key, Jsonb(payload), artifact_hashes[key]),
                )
            target.execute(
                "UPDATE rockygpt_v2.dataset_versions SET status='active',activated_at=now() WHERE id=%s", (dataset_id,),
            )
            target.execute(
                "INSERT INTO rockygpt_v2.releases(version,dataset_version_id,status,activated_at,quality_summary) "
                "VALUES(%s,%s,'active',now(),%s)",
                (args.version, dataset_id, Jsonb({"source_dataset_version": source_version,
                    "identity_hash": identity_hash, "profile_artifact_hashes": artifact_hashes})),
            )
            # A separate runtime role can SELECT only public campus evidence.
            if not target.execute("SELECT 1 FROM pg_roles WHERE rolname='brain_campus_reader'").fetchone():
                target.execute("CREATE ROLE brain_campus_reader LOGIN")
                target.execute("ALTER ROLE brain_campus_reader SET default_transaction_read_only=on")
            target.execute("GRANT USAGE ON SCHEMA rockygpt_v2 TO brain_campus_reader")
            for table in (*TABLES, "dataset_versions", "releases"):
                target.execute(sql.SQL("GRANT SELECT ON rockygpt_v2.{} TO brain_campus_reader").format(sql.Identifier(table)))
    report = {
        "created_at": datetime.now(UTC).isoformat(), "source_read_only": True,
        "source_dataset_version": source_version, "dataset_id": str(dataset_id),
        "dev_dataset_version": args.version, "data_commit": args.data_commit,
        "identity_hash": identity_hash, "identity_entities": len(identity["entities"]),
        "profile_artifact_hashes": artifact_hashes,
        "target_host": target_parts["host"], "target_port": target_parts["port"],
        "target_database": target_parts["dbname"], "record_counts": counts,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
