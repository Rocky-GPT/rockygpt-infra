#!/usr/bin/env python3
"""Restore only checkpoints produced by this workflow on the default branch."""

import json
import os
import subprocess


def api(path):
    return json.loads(subprocess.check_output(["gh", "api", path], stderr=subprocess.DEVNULL))


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    current = int(os.environ["GITHUB_RUN_ID"])
    runs = api(f"repos/{repo}/actions/workflows/environment-sync.yml/runs?branch=main&per_page=100")
    for run in runs["workflow_runs"]:
        if run["id"] == current or run["event"] not in {"schedule", "workflow_dispatch"}:
            continue
        if run["status"] != "completed":
            raise RuntimeError("A previous sync is unfinished")
        artifacts = api(f"repos/{repo}/actions/runs/{run['id']}/artifacts")
        state = [a for a in artifacts["artifacts"] if a["name"] == "environment-sync-state"]
        if state:
            if state[0]["expired"]:
                raise RuntimeError("Sync state expired; review hosting state before a new bootstrap")
            with open(os.environ["GITHUB_OUTPUT"], "a") as output:
                output.write(f"run_id={run['id']}\n")
            return
        # Do not silently roll back to an older checkpoint after a killed run.
        if run["conclusion"] in {"cancelled", "timed_out"}:
            raise RuntimeError("Previous sync interrupted without a checkpoint; review hosting state")


if __name__ == "__main__":
    main()
