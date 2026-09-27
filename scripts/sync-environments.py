#!/usr/bin/env python3
"""One-way production environment sync. Dry-run unless --apply is supplied.

Values are held in memory only. State contains keyed fingerprints and deployment
IDs, never environment values. HTTP/SDK errors are intentionally not echoed.
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class SyncError(Exception):
    """A diagnostic that is safe to print without exposing a credential."""


def require(condition, message):
    if not condition:
        raise SyncError(message)


def fingerprint(secret, target, values):
    payload = json.dumps([target, values], sort_keys=True, separators=(",", ":"))
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def selected_values(variables, keys):
    values = {}
    for variable in variables:
        name, value = variable.name, variable.value
        require(name not in values, "Duplicate variable in 1Password Environment")
        values[name] = value
    require(len(keys) == len(set(keys)) and bool(keys), "Invalid managed-key list")
    require(all(re.fullmatch(r"[A-Z][A-Z0-9_]*", k) for k in keys), "Invalid variable name")
    require(all(isinstance(values.get(k), str) and values[k] for k in keys),
            "A managed variable is missing or empty in 1Password; nothing will be deleted")
    return {key: values[key] for key in keys}


def desired_values(variables, target):
    """Secrets come from 1Password; approved public settings are versioned here."""
    allowed = {"render": {"BRAIN_ENVIRONMENT", "BRAIN_OPENAI_PROJECT", "BRAIN_ROUTING_MODE", "BRAIN_ROUTING_PROVIDER"},
               "vercel": {"BRAIN_URL"}}
    settings = target.get("settings", {})
    require(isinstance(settings, dict) and set(settings) <= allowed.get(target["provider"], set()),
            "Unapproved variable in public settings")
    require(not set(settings).intersection(target["keys"]), "Secret and setting names overlap")
    require(all(isinstance(value, str) and value for value in settings.values()), "A public setting is empty")
    return selected_values(variables, target["keys"]) | settings


class API:
    def __init__(self, base, token, query=None):
        self.base, self.token, self.query = base, token, query or {}

    def call(self, method, path, body=None, query=None):
        url = self.base + path
        params = self.query | (query or {})
        if params:
            url += "?" + urlencode(params)
        headers = {"Authorization": "Bearer " + self.token,
                   "Content-Type": "application/json", "User-Agent": "RockyGPT-env-sync/1.0"}
        data = None if body is None else json.dumps(body).encode()
        try:
            with urlopen(Request(url, data=data, headers=headers, method=method), timeout=45) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except HTTPError as error:
            code = error.code
            error.close()
            raise SyncError(f"Hosting API request failed (HTTP {code}); response body withheld") from None
        except (URLError, TimeoutError, ValueError):
            raise SyncError("Hosting API request failed; details withheld") from None


class Render:
    def __init__(self, target, token):
        self.target = target
        self.api = API("https://api.render.com/v1", token)
        self.path = "/services/" + quote(target["service_id"], safe="")

    def rows(self, resource):
        result, cursor, seen = [], None, set()
        for _ in range(100):
            params = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            page = self.api.call("GET", self.path + resource, query=params)
            require(isinstance(page, list), "Unexpected Render response")
            result.extend(page)
            if len(page) < 100:
                return result
            cursor = page[-1].get("cursor")
            require(cursor and cursor not in seen, "Render pagination did not advance")
            seen.add(cursor)
        raise SyncError("Render pagination limit exceeded")

    def changes(self, desired, previous, revision, bootstrap):
        rows = self.rows("/env-vars")
        current = {row["envVar"]["key"]: row["envVar"]["value"] for row in rows}
        return [key for key, value in desired.items() if current.get(key) != value]

    def base_revision(self):
        deploys = [row["deploy"] for row in self.rows("/deploys")]
        require(not any(d["status"] in {"created", "queued", "build_in_progress", "pre_deploy_in_progress", "update_in_progress"}
                        for d in deploys), "Render already has a deployment in progress; try again later")
        live = next((d for d in deploys if d["status"] == "live"), None)
        require(live is not None, "No live Render deployment found")
        sha = live.get("commit", {}).get("id", "")
        require(bool(re.fullmatch(r"[a-f0-9]{40}", sha)), "Live Render commit is unavailable")
        return sha

    def put(self, key, value):
        self.api.call("PUT", self.path + "/env-vars/" + quote(key, safe=""), {"value": value})

    def deploy(self, base):
        result = self.api.call("POST", self.path + "/deploys", {"commitId": base})
        return result["id"]

    def status(self, deployment):
        status = self.api.call("GET", self.path + "/deploys/" + quote(deployment, safe=""))["status"]
        if status == "live":
            return "ready"
        if status in {"build_failed", "pre_deploy_failed", "update_failed", "canceled", "deactivated"}:
            return "failed"
        return "pending"


class Vercel:
    def __init__(self, target, token):
        self.target, self.envs = target, {}
        self.api = API("https://api.vercel.com", token, {"slug": target["team_slug"]})
        self.project = quote(target["project"], safe="")

    def changes(self, desired, previous, revision, bootstrap):
        response = self.api.call("GET", f"/v10/projects/{self.project}/env")
        rows = response["envs"]
        selected = [row for row in rows if "production" in row.get("target", [])
                    and not row.get("gitBranch") and not row.get("customEnvironmentIds")]
        require(len({r["key"] for r in selected}) == len(selected), "Duplicate Vercel production variable")
        self.envs = {row["key"]: row for row in selected}
        for key in desired:
            if key in self.envs:
                require(self.envs[key]["target"] == ["production"],
                        "A managed Vercel variable is shared with preview; split its scope before syncing")
                require(self.envs[key].get("type") == "sensitive",
                        "A managed Vercel variable must be Sensitive before syncing")
        require(bool(previous) or bootstrap,
                "Vercel has no trusted sync state; a reviewed bootstrap run is required")
        # Sensitive values cannot be retrieved. A server-side edit invalidates
        # the recorded metadata, even when the 1Password value has not changed.
        metadata = self.metadata(desired)
        if previous.get("revision") == revision and previous.get("metadata") == metadata:
            return []
        return list(desired)

    def metadata(self, desired):
        return {key: {field: self.envs.get(key, {}).get(field) for field in ("id", "updatedAt", "type")}
                for key in desired}

    def base_revision(self):
        project = self.api.call("GET", f"/v9/projects/{self.project}")
        deployments = self.api.call("GET", "/v7/deployments", query={"projectId": project["id"],
                                    "target": "production", "limit": 100})["deployments"]
        require(not any(d.get("state", d.get("readyState")) in {"BUILDING", "QUEUED", "INITIALIZING"}
                        for d in deployments), "Vercel already has a production deployment in progress")
        live = project.get("targets", {}).get("production") or {}
        require(live.get("readyState") == "READY", "No ready Vercel production deployment found")
        sha = live.get("meta", {}).get("githubCommitSha", "")
        link = project.get("link", {})
        require(link.get("type") == "github" and link.get("repoId") and re.fullmatch(r"[a-f0-9]{40}", sha),
                "Vercel production Git source could not be verified")
        return {"type": "github", "repoId": link["repoId"], "ref": sha, "sha": sha}

    def put(self, key, value):
        body = {"key": key, "value": value, "type": "sensitive", "target": ["production"]}
        if key in self.envs:
            # Vercel rejects the key field for a Sensitive variable, even when
            # the name is unchanged. Preserve its existing name/type/scope.
            self.api.call("PATCH", f"/v9/projects/{self.project}/env/" + quote(self.envs[key]["id"], safe=""),
                          {"value": value})
        else:
            self.api.call("POST", f"/v10/projects/{self.project}/env", body)

    def deploy(self, base):
        # deploymentId can reuse the original environment. A pinned Git source
        # rebuilds the live code using the newly configured project variables.
        result = self.api.call("POST", "/v13/deployments", {"name": self.target["project"],
                               "project": self.target["project"], "target": "production", "gitSource": base},
                               query={"forceNew": "1"})
        return result["id"]

    def status(self, deployment):
        result = self.api.call("GET", "/v13/deployments/" + quote(deployment, safe=""))
        if result["readyState"] == "READY" and result.get("aliasAssigned") and not result.get("aliasError"):
            return "ready"
        if result["readyState"] in {"ERROR", "CANCELED", "BLOCKED"} or result.get("aliasError"):
            return "failed"
        return "pending"


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, sort_keys=True, indent=2) + "\n")
    temporary.replace(path)


def check_health(url):
    try:
        with urlopen(url, timeout=30) as response:
            require(response.status == 200, "Readiness check did not succeed")
            payload = json.load(response)
            require(payload.get("status") == "ready", "Readiness response is not ready")
    except HTTPError as error:
        error.close()
        raise SyncError("Readiness check did not succeed") from None
    except (URLError, TimeoutError, ValueError):
        raise SyncError("Readiness check did not succeed") from None


def sync_target(target, desired, provider, state, state_path, secret, apply=False, bootstrap=False):
    name = target["name"]
    previous = state.get(name, {})
    revision = fingerprint(secret, target, desired)
    # Finish an accepted deployment before attempting further writes.
    if previous.get("deployment") and previous.get("pending"):
        status = provider.status(previous["deployment"])
        require(status != "pending", f"{name}: the saved deployment is still running")
        require(status != "failed" or previous.get("revision") != revision,
                f"{name}: the saved deployment failed; inspect it and repair the 1Password source")
        if status == "ready":
            check_health(target["readiness_url"])
            if apply:
                previous["pending"] = False
                save_state(state_path, state)
    changes = provider.changes(desired, previous, revision, bootstrap)
    pending = previous.get("pending", False)
    print(f"{name}: {len(changes)} managed variables need syncing; deployment pending: {pending}")
    if not apply:
        return
    if not changes and not pending:
        state[name] = previous | {"revision": revision, "pending": False}
        save_state(state_path, state)
        return
    base = provider.base_revision()  # Before any production mutation.
    state[name] = {"revision": revision, "pending": True}
    save_state(state_path, state)  # Preserve the pending flag through partial writes.
    for key in changes:
        provider.put(key, desired[key])
    if isinstance(provider, Vercel):
        # Read back metadata only; never request decryption of sensitive values.
        provider.changes(desired, state[name], revision, True)
        state[name]["metadata"] = provider.metadata(desired)
        save_state(state_path, state)
    deployment = provider.deploy(base)
    state[name]["deployment"] = deployment
    save_state(state_path, state)
    print(f"{name}: deployment {deployment} started")
    for _ in range(80):
        status = provider.status(deployment)
        if status == "ready":
            check_health(target["readiness_url"])
            state[name]["pending"] = False
            save_state(state_path, state)
            print(f"{name}: deployed and ready")
            return
        require(status != "failed", f"{name}: deployment failed; inspect hosting dashboard")
        time.sleep(15)
    raise SyncError(f"{name}: deployment is still pending; the next run will check it")


async def main(args):
    config = json.loads(Path(args.config).read_text())
    targets = [t for t in config["targets"] if t.get("enabled")]
    require(bool(targets), "No sync targets are enabled")
    require(len({t['name'] for t in targets}) == len(targets), "Duplicate target names")
    token = os.environ.get("OP_SERVICE_ACCOUNT_TOKEN", "")
    require(bool(token), "OP_SERVICE_ACCOUNT_TOKEN is required")
    from onepassword import Client
    try:
        client = await Client.authenticate(auth=token, integration_name="RockyGPT environment sync", integration_version="v1.0.0")
        credentials = await client.environments.get_variables(config["credentials_environment"])
        required_tokens = sorted({"RENDER_API_KEY" if t["provider"] == "render" else "VERCEL_TOKEN" for t in targets})
        credentials = selected_values(credentials.variables, required_tokens)
        # Validate every enabled source before the first external write.
        prepared = []
        for target in targets:
            require(target["provider"] in {"render", "vercel"}, "Unsupported provider")
            source = await client.environments.get_variables(target["environment_id"])
            values = desired_values(source.variables, target)
            provider = (Render(target, credentials["RENDER_API_KEY"]) if target["provider"] == "render"
                        else Vercel(target, credentials["VERCEL_TOKEN"]))
            prepared.append((target, values, provider))
    except SyncError:
        raise
    except Exception:
        raise SyncError("1Password source read failed; check service-account access (details withheld)") from None
    path = Path(args.state)
    state = json.loads(path.read_text()) if path.exists() else {}
    for target, values, provider in prepared:
        sync_target(target, values, provider, state, path, token, args.apply, args.bootstrap)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/environment-sync.json")
    parser.add_argument("--state", default=".sync-state/state.json")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--bootstrap", action="store_true", help="Allow first Vercel write from a reviewed source")
    try:
        asyncio.run(main(parser.parse_args()))
    except SyncError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except Exception:
        print("Sync stopped unexpectedly; diagnostic details withheld to protect secrets", file=sys.stderr)
        sys.exit(1)
