"""Run one committed Brain revision against an isolated localhost profile dataset.

Uses the existing Brain virtualenv and development secrets, without importing
the mutable working tree. No branch changes, production writes, or force kills.
"""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import shlex
import signal
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import psycopg
from dotenv import dotenv_values, set_key
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def runtime_database(value: str) -> dict[str, str]:
    parts = conninfo_to_dict(value)
    if (
        set(parts) - {"host", "port", "dbname", "user", "sslmode"}
        or any(os.getenv(key) for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS"))
        or parts.get("host") != "127.0.0.1"
        or parts.get("user") != "brain_campus_reader"
        or not re.fullmatch(r"rockygpt_profiles_dev_[a-z0-9_]+", parts.get("dbname", ""))
        or len(parts.get("dbname", "")) > 63
        or not parts.get("port", "").isdigit()
        or not 1 <= int(parts.get("port", "0")) <= 65535
        or parts.get("sslmode", "disable") != "disable"
    ):
        raise ValueError("Require a named localhost profile database and brain_campus_reader")
    return {**parts, "hostaddr": "127.0.0.1", "sslmode": "disable"}


def committed_revision(brain: Path, value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("Pass the full 40-character committed Brain SHA")
    resolved = subprocess.check_output(
        ["git", "-C", str(brain), "rev-parse", "--verify", value + "^{commit}"], text=True
    ).strip()
    if resolved != value:
        raise ValueError("Revision did not resolve to the requested commit")
    return resolved


def tree_hash(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Build snapshots cannot contain symbolic links")
        if path.is_file() and path.name != ".brain-build.json":
            digest.update(path.relative_to(directory).as_posix().encode() + b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


def immutable_build(brain: Path, builds: Path, revision: str) -> Path:
    archive = subprocess.check_output(["git", "-C", str(brain), "archive", "--format=tar", revision])
    archive_hash = hashlib.sha256(archive).hexdigest()
    destination = builds / revision
    if destination.exists():
        manifest = json.loads((destination / ".brain-build.json").read_text())
        if (manifest.get("revision") != revision or manifest.get("archive_hash") != archive_hash
                or manifest.get("tree_hash") != tree_hash(destination)):
            raise ValueError("Existing immutable build failed integrity verification; refusing to overwrite it")
        return destination
    builds.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".preparing-", dir=builds) as temporary:
        staged = Path(temporary) / "build"
        staged.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            if any(not member.isfile() and not member.isdir() for member in bundle.getmembers()):
                raise ValueError("Git build archive contains unsupported links or special files")
            bundle.extractall(staged, filter="data")
        (staged / ".brain-build.json").write_text(json.dumps({
            "revision": revision, "archive_hash": archive_hash, "tree_hash": tree_hash(staged),
        }, indent=2) + "\n")
        staged.rename(destination)
    for path in destination.rglob("*"):
        path.chmod(0o555 if path.is_dir() or path.stat().st_mode & 0o111 else 0o444)
    destination.chmod(0o555)
    return destination


def owned_brain_command(command: str, brain: Path) -> bool:
    arguments = shlex.split(command)
    interpreters = {str((brain / ".venv/bin/python").resolve()),
                    str((Path(sys.base_prefix) / "Resources/Python.app/Contents/MacOS/Python").resolve())}
    return (
        bool(arguments) and str(Path(arguments[0]).resolve()) in interpreters
        and arguments[1:4] == ["-m", "uvicorn", "rockygpt_brain.api.app:app"]
        and re.search(r"--port(?:=|\s+)8000(?:\s|$)", command) is not None
        and re.search(r"--host(?:=|\s+)127\.0\.0\.1(?:\s|$)", command) is not None
        and "--reload" not in command
    )


def owned_working_directory(value: str, brain: Path) -> bool:
    directory = Path(value).resolve()
    builds = brain.parent / ".local-logs/profile-feature/brain-builds"
    return directory == brain.resolve() or (
        directory.parent == builds.resolve() and re.fullmatch(r"[0-9a-f]{40}", directory.name) is not None
    )


def port_open() -> bool:
    with socket.socket() as connection:
        connection.settimeout(0.5)
        return connection.connect_ex(("127.0.0.1", 8000)) == 0


def stop_owned_brain(pid_file: Path, brain: Path) -> None:
    if pid_file.exists():
        value = pid_file.read_text().strip()
        if not value.isdigit() or int(value) <= 1:
            raise ValueError("Invalid saved Brain PID")
        pid = int(value)
        process = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
        if process.returncode == 0:
            current = subprocess.check_output(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], text=True)
            directories = [line[1:] for line in current.splitlines() if line.startswith("n")]
            if (not owned_brain_command(process.stdout.strip(), brain) or len(directories) != 1
                    or not owned_working_directory(directories[0], brain)):
                raise ValueError("Saved PID is not the expected local Brain; nothing was stopped")
            os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 55
    while port_open():
        if time.monotonic() >= deadline:
            raise RuntimeError("Port 8000 remains occupied; no other PID will be stopped")
        time.sleep(0.2)


def wait_ready(process: subprocess.Popen, expected_hash: str, expected_dataset: str) -> dict:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Archived Brain exited during startup; inspect .local-logs/brain.log")
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/readiness", timeout=3) as response:
                ready = json.load(response)
            if (ready.get("development", {}).get("configurationHash") == expected_hash
                    and ready.get("campus_data", {}).get("dataset_version") == expected_dataset):
                return ready
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            pass
        time.sleep(0.3)
    raise RuntimeError("Archived Brain did not report the expected dev configuration and dataset")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--expected-dataset", required=True)
    args = parser.parse_args()
    workspace = Path(__file__).resolve().parents[2]
    brain = workspace / "rockygpt-brain"
    environment_path = brain / ".env"
    feature = workspace / ".local-logs/profile-feature"
    database = runtime_database(args.database)
    if not args.expected_dataset.startswith("dev-profiles-"):
        raise ValueError("Require the expected dev-profiles dataset version")
    values = dotenv_values(environment_path)
    if values.get("BRAIN_ENVIRONMENT") != "development":
        raise ValueError("Only the existing development Brain environment may be launched")
    revision = committed_revision(brain, args.revision)
    build = immutable_build(brain, feature / "brain-builds", revision)
    runtime_env = {**os.environ, **{key: value for key, value in values.items() if value is not None},
                   "PYTHONPATH": str(build / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    runtime_env.pop("BRAIN_EXPECTED_CONFIG_HASH", None)
    fingerprint = json.loads(subprocess.check_output([
        str(brain / ".venv/bin/python"), "-c",
        "import json,rockygpt_brain; from rockygpt_brain.config import configuration_hash,RELEASE; "
        "print(json.dumps(dict(configurationHash=configuration_hash(),release=RELEASE.version,source=rockygpt_brain.__file__)))",
    ], cwd=build, env=runtime_env, text=True))
    if not Path(fingerprint["source"]).resolve().is_relative_to(build.resolve()):
        raise ValueError("Brain import resolved outside the committed snapshot")
    database_url = make_conninfo(**database)
    with psycopg.connect(database_url, connect_timeout=5) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        row = connection.execute("SELECT version FROM rockygpt_v2.dataset_versions WHERE status='active'").fetchone()
        if not row or row[0] != args.expected_dataset:
            raise ValueError("Local database does not contain the expected candidate release")
        if connection.execute("SELECT has_table_privilege(current_user,'rockygpt_v2.campus_contacts','INSERT')").fetchone()[0]:
            raise ValueError("Runtime campus reader unexpectedly has write privileges")
    feature.mkdir(parents=True, exist_ok=True)
    backup = feature / "brain.env.before"
    if not backup.exists():
        shutil.copy2(environment_path, backup)
        backup.chmod(0o600)
    pid_file = workspace / ".local-logs/pids/brain.pid"
    stop_owned_brain(pid_file, brain)
    # Only these two deployment-owned values change; provider/ledger settings remain intact.
    set_key(environment_path, "DATABASE_URL", database_url)
    set_key(environment_path, "BRAIN_EXPECTED_CONFIG_HASH", fingerprint["configurationHash"])
    runtime_env.update(DATABASE_URL=database_url, BRAIN_EXPECTED_CONFIG_HASH=fingerprint["configurationHash"])
    with (workspace / ".local-logs/brain.log").open("ab") as log:
        process = subprocess.Popen([
            str(brain / ".venv/bin/python"), "-m", "uvicorn", "rockygpt_brain.api.app:app",
            "--host", "127.0.0.1", "--port", "8000",
        ], cwd=build, env=runtime_env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(process.pid) + "\n")
    ready = wait_ready(process, fingerprint["configurationHash"], args.expected_dataset)
    report = {"activated_at": datetime.now(UTC).isoformat(), "environment": "development",
              "revision": revision, "configurationHash": fingerprint["configurationHash"],
              "release": fingerprint["release"], "dataset_version": args.expected_dataset,
              "build": str(build), "pid": process.pid,
              "campus_database": {key: database[key] for key in ("host", "port", "dbname", "user")},
              "identities": ready["development"].get("identities"), "previous_env_backup": str(backup)}
    activations = feature / "activations"
    activations.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    (activations / f"{stamp}-{revision[:12]}.json").write_text(json.dumps(report, indent=2) + "\n")
    (feature / "active-brain.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
