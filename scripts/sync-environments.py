#!/usr/bin/env python3
"""One-way production environment sync. Dry-run unless --apply is supplied.

Targets are Render, Vercel and GitHub Actions secrets. Each run also names (never shows) the
variables a target holds that the manifest does not manage, so a value set by hand in a dashboard
cannot silently make production differ from the laptop, which reads the same manifest.

Values are held in memory only. State contains keyed fingerprints and deployment
IDs, never environment values. HTTP/SDK errors are intentionally not echoed.
"""

import argparse
import asyncio
import base64
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


def desired_values(sources, target):
    """Secrets come from 1Password; approved public settings are versioned here."""
    values = {}
    require(bool(target["sources"]), "No secret sources configured")
    for source in target["sources"]:
        keys = source["keys"]
        require(bool(keys) and len(set(keys)) == len(keys), "Invalid secret source keys")
        if isinstance(keys, list):
            keys = {key: key for key in keys}
        require(isinstance(keys, dict) and all(
            isinstance(key, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
            for key in [*keys, *keys.values()]), "Invalid secret source mapping")
        require(not set(values).intersection(keys), "Duplicate secret source mapping")
        selected = selected_values(sources[source["environment_id"]], list(dict.fromkeys(keys.values())))
        values.update({destination: selected[key] for destination, key in keys.items()})
    settings = target.get("settings", {})
    require(isinstance(settings, dict) and set(settings) <= ALLOWED_SETTINGS.get(target["provider"], set()),
            "Unapproved variable in public settings")
    require(not set(settings).intersection(values), "Secret and setting names overlap")
    require(all(isinstance(value, str) and value for value in settings.values()), "A public setting is empty")
    desired = values | settings
    if target["provider"] == "github":
        # The sync's own token is a bootstrap value, and GitHub reserves the GITHUB_ prefix.
        require(not any(k == "OP_SERVICE_ACCOUNT_TOKEN" or k.startswith("GITHUB_") for k in desired),
                "GitHub target names a secret the sync must never manage")
    return desired


# Public settings each provider may receive. The laptop launcher (rockygpt-workspace local-env.py)
# reads the same manifest, so a setting listed here reaches both production and the laptop.
ALLOWED_SETTINGS = {
    "render": {"BRAIN_ENVIRONMENT", "BRAIN_OPENAI_PROJECT", "BRAIN_OUTPUT", "BRAIN_HOWTO_ONE_CALL",
               "BRAIN_WRITER_MAX_OUTPUT_TOKENS", "BRAIN_OPENAI_EMBED_MODEL",
               "BRAIN_OPENAI_EMBED_INPUT_NUSD_PER_TOKEN", "BRAIN_OPENAI_EMBED_PRICE_VALID_UNTIL"},
    "vercel": {"BRAIN_URL"},
    "github": {"BRAIN_URL", "UI_URL"},
}
PROVIDER_TOKENS = {"render": "RENDER_API_KEY", "vercel": "VERCEL_TOKEN", "github": "GITHUB_SECRETS_TOKEN"}


def unmanaged_names(target, present, desired):
    """Names a target holds that the manifest neither manages nor lists as kept by hand."""
    manual = target.get("manual", [])
    require(isinstance(manual, list) and all(isinstance(k, str) for k in manual), "Invalid manual list")
    return sorted(set(present) - set(desired) - set(manual))


class API:
    def __init__(self, base, token, query=None, headers=None):
        self.base, self.token, self.query, self.headers = base, token, query or {}, headers or {}

    def call(self, method, path, body=None, query=None):
        url = self.base + path
        params = self.query | (query or {})
        if params:
            url += "?" + urlencode(params)
        headers = {"Authorization": "Bearer " + self.token,
                   "Content-Type": "application/json", "User-Agent": "RockyGPT-env-sync/1.0"} | self.headers
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
    redeploys = True

    def __init__(self, target, token):
        self.target, self.unmanaged = target, []
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
        self.unmanaged = unmanaged_names(self.target, current, desired)
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
    redeploys = True

    def __init__(self, target, token):
        self.target, self.envs, self.unmanaged = target, {}, []
        self.api = API("https://api.vercel.com", token, {"slug": target["team_slug"]})
        self.project = quote(target["project"], safe="")

    def changes(self, desired, previous, revision, bootstrap):
        response = self.api.call("GET", f"/v10/projects/{self.project}/env")
        rows = response["envs"]
        selected = [row for row in rows if "production" in row.get("target", [])
                    and not row.get("gitBranch") and not row.get("customEnvironmentIds")]
        require(len({r["key"] for r in selected}) == len(selected), "Duplicate Vercel production variable")
        self.envs = {row["key"]: row for row in selected}
        self.unmanaged = unmanaged_names(self.target, self.envs, desired)
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


def seal(public_key, value):
    """Encrypt a value for GitHub's secrets API (libsodium sealed box), as GitHub requires."""
    from nacl import encoding, public
    box = public.SealedBox(public.PublicKey(public_key.encode(), encoding.Base64Encoder()))
    return base64.b64encode(box.encrypt(value.encode())).decode()


class GitHub:
    """GitHub Actions secrets of one repository, or of one of its deployment environments.

    Secrets are write-only, so like Vercel the checkpoint pairs a keyed fingerprint of the source
    with each secret's update time. Workflows read a new value on their next run; nothing deploys.
    """
    redeploys = False

    def __init__(self, target, token):
        self.target, self.secrets, self.unmanaged, self.key = target, {}, [], None
        self.api = API("https://api.github.com", token, headers={
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        repository = target.get("repository", "")
        require(bool(re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+", repository)), "Invalid GitHub repository")
        environment = target.get("environment")
        require(environment is None or bool(re.fullmatch(r"[A-Za-z0-9._-]+", environment)),
                "Invalid GitHub environment")
        self.path = (f"/repos/{repository}/environments/{environment}/secrets" if environment
                     else f"/repos/{repository}/actions/secrets")

    def rows(self):
        result = []
        for page in range(1, 101):
            body = self.api.call("GET", self.path, query={"per_page": 100, "page": page})
            require(isinstance(body, dict) and isinstance(body.get("secrets"), list), "Unexpected GitHub response")
            result.extend(body["secrets"])
            if len(body["secrets"]) < 100:
                return result
        raise SyncError("GitHub pagination limit exceeded")

    def changes(self, desired, previous, revision, bootstrap):
        self.secrets = {row["name"]: row for row in self.rows()}
        self.unmanaged = unmanaged_names(self.target, self.secrets, desired)
        require(bool(previous) or bootstrap,
                "GitHub has no trusted sync state; a reviewed bootstrap run is required")
        if previous.get("revision") == revision and previous.get("metadata") == self.metadata(desired):
            return []
        return list(desired)

    def metadata(self, desired):
        return {key: self.secrets.get(key, {}).get("updated_at") for key in desired}

    def put(self, key, value):
        if self.key is None:
            self.key = self.api.call("GET", self.path + "/public-key")
            require(isinstance(self.key, dict) and self.key.get("key") and self.key.get("key_id"),
                    "Unexpected GitHub public key response")
        self.api.call("PUT", self.path + "/" + quote(key, safe=""),
                      {"encrypted_value": seal(self.key["key"], value), "key_id": self.key["key_id"]})


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
    unmanaged = getattr(provider, "unmanaged", [])
    if unmanaged:
        # Names only. A GitHub annotation puts this on the run page, not just in the log.
        print(f"::warning::{name}: set outside the manifest (the laptop never sees these; manage them "
              f"or list them as manual): {', '.join(unmanaged)}")
    if not apply:
        return
    if not getattr(provider, "redeploys", True):
        if not changes and not pending:
            state[name] = previous | {"revision": revision, "pending": False}
            save_state(state_path, state)
            return
        # No revision until every write lands, so an interrupted run rewrites everything next time.
        state[name] = {"pending": True}
        save_state(state_path, state)
        for key in changes:
            provider.put(key, desired[key])
        provider.changes(desired, state[name], revision, True)
        state[name] = {"revision": revision, "pending": False, "metadata": provider.metadata(desired)}
        save_state(state_path, state)
        print(f"{name}: {len(changes)} secrets updated; workflows use them on their next run")
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
        credentials_source = await client.environments.get_variables(config["credentials_environment"])
        require(all(t["provider"] in PROVIDER_TOKENS for t in targets), "Unsupported provider")
        required_tokens = sorted({PROVIDER_TOKENS[t["provider"]] for t in targets})
        credentials = selected_values(credentials_source.variables, required_tokens)
        # Validate every enabled source before the first external write.
        prepared = []
        sources = {config["credentials_environment"]: credentials_source.variables}
        for target in targets:
            for source in target["sources"]:
                environment_id = source["environment_id"]
                if environment_id not in sources:
                    result = await client.environments.get_variables(environment_id)
                    sources[environment_id] = result.variables
            values = desired_values(sources, target)
            kind = {"render": Render, "vercel": Vercel, "github": GitHub}[target["provider"]]
            provider = kind(target, credentials[PROVIDER_TOKENS[target["provider"]]])
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
    parser.add_argument("--bootstrap", action="store_true",
                        help="Allow the first Vercel or GitHub write from a reviewed source")
    try:
        asyncio.run(main(parser.parse_args()))
    except SyncError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except Exception:
        print("Sync stopped unexpectedly; diagnostic details withheld to protect secrets", file=sys.stderr)
        sys.exit(1)
